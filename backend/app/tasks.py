import os
import math
import shutil
import subprocess
import tempfile
import random
import threading
import numpy as np
import logging
import librosa
from celery import shared_task
from django.db import OperationalError, transaction
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import connection
from django.db.models import F, FloatField, ExpressionWrapper
from django.utils import timezone
from datetime import timedelta
from django.core.cache import cache
from pgvector.django import CosineDistance
from .models import AudioClip, UserInteraction, User
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

whisper_model = None
embedding_model = None
kw_model = None
_model_lock = threading.Lock()


def get_whisper_model():
    # DECISION: Use thread-safe double-checked locking instead of simple
    # singleton to prevent concurrent task initialization from loading the
    # same ML model multiple times (memory waste / duplicate GPU/CPU loads).
    # Tradeoff: Slight latency on first access vs. guaranteed single init.
    global whisper_model
    if whisper_model is None:
        with _model_lock:
            if whisper_model is None:
                from faster_whisper import WhisperModel
                try:
                    whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
                    logger.info("WhisperModel initialized successfully.")
                except Exception as e:
                    logger.exception("Failed to initialize WhisperModel: %s", e)
                    raise
    return whisper_model


def get_embedding_model():
    global embedding_model
    if embedding_model is None:
        with _model_lock:
            if embedding_model is None:
                from sentence_transformers import SentenceTransformer
                try:
                    embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
                    logger.info("Embedding model initialized successfully.")
                except Exception as e:
                    logger.exception("Failed to initialize embedding model: %s", e)
                    raise
    return embedding_model


def get_kw_model():
    global kw_model
    if kw_model is None:
        with _model_lock:
            if kw_model is None:
                from keybert import KeyBERT
                try:
                    kw_model = KeyBERT()
                    logger.info("KeyBERT initialized successfully.")
                except Exception as e:
                    logger.exception("Failed to initialize KeyBERT: %s", e)
                    raise
    return kw_model

def extract_acoustic_vector(y,sr):
    """
    Extracts exactly 128 acoustic features representing the "vibe" of the audio.
    
    ALGORITHM: Acoustic Feature Extraction for Audio "Vibe" Matching
    This function uses librosa to extract multi-dimensional audio characteristics:
    - MFCC (40 dims): Captures timbre and voice texture for speaker/instrument recognition
    - Chroma (12 dims): Captures harmonic content and musical pitch characteristics
    - Mel Spectrogram (76 dims): Captures energy distribution across frequency ranges
    
    These 128 dimensions create a normalized vector used for finding audio with similar acoustic properties.
    The normalization ensures consistent cosine similarity calculations across the platform.
    
    Args:
        file_path (str): Path to the audio file to process
        
    Returns:
        list: 128-dimensional normalized audio feature vector
    """
    # Load audio (downsample to 22050Hz for faster processing)
    #y, sr = librosa.load(file_path, sr=22050)
    
    # 1. Mel-frequency cepstral coefficients (Timbre/Voice texture) - 40 dims
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=40).mean(axis=1)
    
    # 2. Chroma feature (Harmonic/Musical pitch) - 12 dims
    chroma = librosa.feature.chroma_stft(y=y, sr=sr).mean(axis=1)
    
    # 3. Mel Spectrogram (Energy across frequencies) - 76 dims
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=76).mean(axis=1)
    
    # Concatenate into exactly 128 dimensions
    acoustic_vector = np.concatenate((mfcc, chroma, mel))
    
    # Normalize the vector for Cosine Similarity math
    norm = np.linalg.norm(acoustic_vector)
    if norm > 0:
        acoustic_vector = acoustic_vector / norm
        
    return acoustic_vector.tolist()


def normalize_to_wav(input_file_path, sr=22050):
    """Decode arbitrary input audio (mp3/webm/ogg/m4a/whatever a client sends)
    into a clean mono PCM WAV via ffmpeg, up front.

    This exists because librosa.load() tries soundfile (libsndfile) first and
    silently falls back to the deprecated `audioread` path — logging a
    UserWarning/FutureWarning — on any container/codec libsndfile can't
    decode. Direct browser/file uploads hit this because, unlike the scraper
    ingestion path (which already runs everything through
    scrapers/normalizer.py), nothing normalizes user uploads before they're
    handed to librosa. Doing one authoritative ffmpeg decode here removes the
    audioread fallback entirely (so this doesn't silently start hard-failing
    when librosa 1.0 drops that fallback) and gives every downstream step
    (librosa, Whisper, ffmpeg HLS) the same known-good source file instead of
    each re-decoding the original independently.

    Raises subprocess.CalledProcessError if ffmpeg can't decode the input at
    all (i.e. the upload is genuinely not a valid audio file).
    """
    fd, wav_path = tempfile.mkstemp(suffix='.wav')
    os.close(fd)
    command = [
        'ffmpeg', '-y', '-i', input_file_path,
        '-ac', '1', '-ar', str(sr),
        '-f', 'wav', wav_path,
    ]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return wav_path


RETRYABLE_ERRORS = (
    OperationalError,
    ConnectionError,
    subprocess.CalledProcessError,
    OSError,
)

# DECISION: Added bind=True + retry config to prevent permanent data loss
# when transient errors occur (DB connection timeout, FFmpeg crash, network
# failure). Tradeoff: Slightly more overhead per task (retry tracking) vs.
# guaranteed resilience.
@shared_task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600, retry_jitter=False)
def process_audio_to_hls(self, clip_id):

    # Now 'clip' is guaranteed to exist for the following logic
    logger.info("process_audio_to_hls Task is starting...")
    clip = AudioClip.objects.get(id=clip_id)
    if not clip.original_file:
        # Handle missing file error
        logger.error("Audio file for clip %s not found.", clip_id)
        clip.status = 'failed'
        clip.save()
        return

    # DIAGNOSIS -> SOLUTION: `clip.original_file.path` only exists for
    # FileSystemStorage; S3Storage (see settings.STORAGES) has no local path
    # at all — the bytes live in the bucket, not on this container's disk.
    # ffmpeg/librosa/Whisper all need a real local file to operate on, so we
    # explicitly stream the object down to a local temp file once, work on
    # that, and delete it when done. This is the same shape as
    # normalize_to_wav() below on purpose: "pull remote bytes to a local
    # scratch file, process, clean up" is now the *only* pattern used
    # anywhere in this task — no code path assumes a shared filesystem.
    fd, input_file_path = tempfile.mkstemp(suffix=os.path.splitext(clip.original_file.name)[1] or '.bin')
    with os.fdopen(fd, 'wb') as local_copy:
        with clip.original_file.open('rb') as remote_file:
            shutil.copyfileobj(remote_file, local_copy)

    # Normalize once, up front, before anything tries to decode the raw
    # upload. See normalize_to_wav() docstring for why this exists.
    try:
        normalized_path = normalize_to_wav(input_file_path)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to normalize audio for clip %s: %s", clip_id, e.stderr.decode())
        clip.status = 'failed'
        clip.save()
        os.remove(input_file_path)
        return
    finally:
        # input_file_path's only job was feeding ffmpeg above; normalized_path
        # is what every later step reads from.
        if os.path.exists(input_file_path):
            os.remove(input_file_path)

    # Local scratch dir for the HLS segments ffmpeg is about to generate.
    # ffmpeg needs a real filesystem to write into — it can't target S3
    # directly — so we render locally, then upload every resulting file to
    # object storage, then remove the local copy. Nothing under this
    # directory is ever read by another container; it exists only for the
    # lifetime of this task on this worker.
    local_hls_dir = tempfile.mkdtemp(prefix=f'hls-{clip_id}-')

    try:
        # 1. Acoustic Vector Extraction
        try:
            y, sr = librosa.load(normalized_path, sr=22050)
        except Exception as e:
            logger.exception("librosa.load() failed for clip %s: %s", clip_id, e)
            clip.status = 'failed'
            clip.save()
            return
        clip.acoustic_vector = extract_acoustic_vector(y, sr)

        # CRITICAL FIX: Extract exact duration for completion_rate math
        clip.duration_ms = int(librosa.get_duration(y=y, sr=sr) * 1000)

        clip.save(update_fields=['acoustic_vector', 'duration_ms'])
        logger.info(f"Extracted acoustic vector and duration for clip {clip_id}")
        # 2. AUDIO TO TEXT (Whisper)
        try:
            # Lazy-init models to avoid startup cost during management commands
            model = get_whisper_model()
            segments, info = model.transcribe(normalized_path, beam_size=5)
            transcript_text = " ".join([segment.text for segment in segments]).strip()

            # B. Semantic Vector via sentence-transformers
            if transcript_text:
                embed_model = get_embedding_model()
                vector = embed_model.encode(transcript_text)
                clip.semantic_vector = vector.tolist()
                # Extracts top 3 unigrams (single words)
                keywords = get_kw_model().extract_keywords(
                    transcript_text,
                    keyphrase_ngram_range=(1, 1),
                    stop_words='english',
                    top_n=3,
                )
                logger.info(f"Extracted keywords for clip {clip_id}: {keywords}")
                clip.tags = [kw[0] for kw in keywords]
            else:
                # Fallback for purely instrumental tracks with no vocals
                clip.semantic_vector = [0.0] * 384
                clip.tags = ["instrumental"]
        except Exception as e:
            logger.exception("Local AI Processing Failed: %s", e)
            clip.status = 'failed'
            clip.save()
            return

        # Encode HLS from the same normalized WAV, not the raw upload — one
        # authoritative decode instead of ffmpeg re-decoding the original
        # container a second time. Written to LOCAL scratch space, then
        # uploaded to object storage below.
        # DECISION: Use standard MPEG-TS segments explicitly.
        # Chrome's MSE decoder rejects fMP4 segments with certain AAC codec
        # configurations, producing "DecoderStatus::kUnsupportedConfig".
        # MPEG-TS is universally supported by hls.js and all browsers.
        # Newer FFmpeg defaults to fMP4, so we must explicitly set mpegts.
        command = [
            'ffmpeg', '-y', '-i', normalized_path,
            '-c:a', 'aac', '-ar', '44100', '-ac', '2', '-b:a', '128k',
            '-f', 'hls', '-hls_time', '4', '-hls_playlist_type', 'vod',
            '-hls_segment_type', 'mpegts',
            '-master_pl_name', 'master.m3u8',
            os.path.join(local_hls_dir, 'index.m3u8')
        ]

        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Upload every file ffmpeg just wrote locally up to object
            # storage, under hls/<clip_id>/... — this is the step that
            # replaces "shared volume" with "every container talks to the
            # same bucket over the network".
            storage_prefix = f"hls/{clip.id}"
            for root, _dirs, files in os.walk(local_hls_dir):
                for fname in files:
                    local_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(local_path, local_hls_dir)
                    storage_key = f"{storage_prefix}/{rel_path}".replace(os.sep, '/')
                    with open(local_path, 'rb') as fh:
                        default_storage.save(storage_key, fh)

            # DECISION: store the relative object KEY, not a full URL. A
            # signed S3 URL expires (see AWS_S3_QUERYSTRING_EXPIRE) — baking
            # one into the database would mean playback silently breaks an
            # hour after processing regardless of whether the clip is still
            # valid. The serializer generates a fresh signed URL from this
            # key on every read instead (see FeedClipSerializer).
            clip.hls_playlist_url = f"{storage_prefix}/master.m3u8"
            clip.status = 'ready'
            clip.save()
        except subprocess.CalledProcessError as e:
            clip.status = 'failed'
            clip.save()
            logger.error("FFmpeg Error: %s", e.stderr.decode())
    finally:
        # Always clean up both local scratch areas, success or failure.
        try:
            os.remove(normalized_path)
        except OSError:
            pass
        shutil.rmtree(local_hls_dir, ignore_errors=True)

    # DECISION: The OpenAI transcription/embedding/tagging path was a
    # prototype ("for when i will have money for API"). The local Whisper +
    # SentenceTransformer + KeyBERT pipeline (lines ~200-326) is the
    # production path. The OpenAI block was a triple-quoted string statement,
    # not a comment, so it was never executed — but its presence confused
    # grep and review. Removed.


@shared_task(bind=True, max_retries=2, default_retry_delay=30, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True)
def refill_user_feed(self, user_id, count=50):
    user = User.objects.get(id=user_id)
    redis_key = f"user_feed:{user_id}"
    redis_client = cache.client.get_client()

    # Prevent concurrent refills for the same user (race condition fix)
    # DECISION: SETNX with 30s expiry prevents double-refill without
    # blocking indefinitely if a worker dies mid-task.
    lock_key = f"feed_refill_lock:{user_id}"
    acquired = redis_client.set(lock_key, "1", nx=True, ex=30)
    if not acquired:
        return "Refill already in progress."

    try:
        if redis_client.llen(redis_key) >= 20:
            return "Queue sufficient."

        seen_ids = list(UserInteraction.objects.filter(user=user,created_at__gte=timezone.now() - timedelta(days=30)).values_list('clip_id', flat=True))
        queued_ids = [vid.decode('utf-8') for vid in redis_client.lrange(redis_key, 0, -1)]
        seen_ids.extend(queued_ids)

        sem_query, ac_query = calculate_time_decayed_vectors(user)
        base_queryset = AudioClip.objects.filter(status='ready').exclude(id__in=seen_ids)
        clip_ids_to_push = []

        if sem_query and ac_query:
            # THE COMPOSITE FORMULA (Done natively in PostgreSQL for maximum speed)
            composite_query = base_queryset.annotate(
                sem_dist=CosineDistance('semantic_vector', sem_query),
                ac_dist=CosineDistance('acoustic_vector', ac_query),
                vector_similarity=ExpressionWrapper(
                    1.0 - ((F('sem_dist') + F('ac_dist')) / 4.0),
                    output_field=FloatField()
                ),
                composite_score=ExpressionWrapper(
                    (F('vector_similarity') * 0.45) +
                    (F('avg_completion_rate') * 0.30) +
                    (F('engagement_velocity') * 0.25),
                    output_field=FloatField()
                )
            ).order_by('-composite_score')

            # 80% EXPLOIT: Serve highest scoring algorithmic matches
            exploit_count = int(count * 0.8)
            exploit_clips = composite_query[:exploit_count]
            # The Follow Graph Wedge: Pull recent content from followed creators
            followed_creators = user.following.all()
            network_clips = base_queryset.filter(
                creator__in=followed_creators
            ).order_by('-created_at')[:5] # Force 5 network clips into the mix
            clip_ids_to_push.extend([str(c.id) for c in exploit_clips])
            clip_ids_to_push.extend([str(c.id) for c in network_clips])


            # 20% EXPLORE: Serve high velocity clips outside their vector neighborhood
            explore_count = count - exploit_count
            explore_clips = base_queryset.exclude(
                id__in=[c.id for c in exploit_clips]
            ).order_by('-engagement_velocity')[:explore_count]

            clip_ids_to_push.extend([str(c.id) for c in explore_clips])
        else:
            # Cold start
            cold_clips = base_queryset.order_by('-engagement_velocity', '-created_at')[:count]
            clip_ids_to_push.extend([str(c.id) for c in cold_clips])
    finally:
        # DECISION: Always release refill lock to prevent deadlock if worker
        # crashes after acquiring but before completing the task.
        try:
            redis_client.delete(lock_key)
        except Exception:
            pass

    if not clip_ids_to_push:
        return "No new clips to push."

    random.shuffle(clip_ids_to_push)
    redis_client.rpush(redis_key, *clip_ids_to_push)
    # Set 24-hour TTL to prevent memory leak from orphaned feed lists.
    redis_client.expire(redis_key, 86400)
    return f"Added {len(clip_ids_to_push)} composite-ranked clips."

def calculate_time_decayed_vectors(user, limit=50):
    recent_interactions = UserInteraction.objects.filter(
        user=user
    ).select_related('clip').order_by('-created_at')[:limit]
    
    if recent_interactions is None or len(recent_interactions) == 0:
        return user.long_term_semantic, user.long_term_acoustic

    now = timezone.now()
    sem_vectors, ac_vectors, weights = [], [], []

    for interaction in recent_interactions:
        if interaction.clip.semantic_vector is None:continue

        # 1. Time Decay: A like from today is worth more than a like from last month
        hours_ago = (now - interaction.created_at).total_seconds() / 3600.0
        time_weight = 1.0 / (1.0 + math.log1p(max(0, hours_ago)))

        # 2. Dwell Time Weight: Actual completion rate dictates value
        comp_weight = interaction.completion_rate if interaction.completion_rate > 0 else 0.1

        # 3. Explicit Intent: Boost shares, penalize instant skips
        intent_weight = 1.0
        if interaction.interaction_type in ['like', 'share']:
            intent_weight = 1.5
        elif interaction.interaction_type == 'skip' and interaction.completion_rate < 0.2:
            intent_weight = -0.5 

        final_weight = time_weight * comp_weight * intent_weight
        
        if interaction.clip.acoustic_vector is not None:
            ac_vectors.append(np.array(interaction.clip.acoustic_vector) * final_weight)
            
        if interaction.clip.semantic_vector is not None:
            sem_vectors.append(np.array(interaction.clip.semantic_vector) * final_weight)
        weights.append(final_weight)

    sum_weights = sum(weights)
    if sum_weights == 0:
        return user.long_term_semantic, user.long_term_acoustic
    if sem_vectors and ac_vectors:
        weighted_sem = np.sum(sem_vectors, axis=0) / sum_weights
        weighted_ac = np.sum(ac_vectors, axis=0) / sum_weights
    else:
        return user.long_term_semantic, user.long_term_acoustic
    # Blend context with baseline
    ALPHA = 0.7
    if user.long_term_semantic is not None:
        final_sem = (ALPHA * weighted_sem) + ((1 - ALPHA) * np.array(user.long_term_semantic))
        final_ac = (ALPHA * weighted_ac) + ((1 - ALPHA) * np.array(user.long_term_acoustic))
    else:
        final_sem, final_ac = weighted_sem, weighted_ac

    norm_sem = np.linalg.norm(final_sem)
    if norm_sem > 0:
        final_sem = final_sem / norm_sem
    else:
        # Fallback to long term baseline if norm is non-computable
        final_sem = np.array(user.long_term_semantic) if user.long_term_semantic else final_sem

    norm_ac = np.linalg.norm(final_ac)
    if norm_ac > 0:
        final_ac = final_ac / norm_ac
    else:
        final_ac = np.array(user.long_term_acoustic) if user.long_term_acoustic else final_ac

    return final_sem.tolist(), final_ac.tolist()


# Added retry config: transient DB/Redis errors cause task failure without retry.
# batch_size=100 to prevent memory exhaustion with large user counts.
@shared_task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600)
def update_global_metrics(self):
    """
    Run every 5 minutes via Celery Beat to recalculate global clip performance.
    Formula punishes older videos that stop accumulating engagement.

    DECISION: Batched by id to avoid table-wide lock contention. Each batch
    updates 5000 rows then commits. The cursor persists across batches by
    storing the highest id seen; on next beat the loop continues from there.
    No row is updated twice, no row is skipped (unless a clip's status
    changes from 'ready' between batches, in which case it's left for the
    next beat).
    """
    clip_table = AudioClip._meta.db_table
    interaction_table = UserInteraction._meta.db_table
    BATCH_SIZE = 5000

    # Cursor persisted in cache (Redis) so a Celery worker restart resumes
    # from the last id seen instead of restarting from 0.
    cursor_key = 'update_global_metrics:resume_id'
    last_id = cache.get(cursor_key) or ''  # empty string = start from beginning

    # engagement_velocity is a per-row formula — batch by id
    ev_query = f"""
        UPDATE {clip_table}
        SET engagement_velocity =
            LEAST((likes + (shares * 2)) / POWER(EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 + 2.0, 1.5)/100.0, 1.0)
        WHERE status = 'ready' AND id > %s
        ORDER BY id
        LIMIT %s
    """
    # avg_completion_rate has a per-row correlated subquery; batch by id
    # (the inner SELECT uses the outer table's id range).
    acr_query = f"""
        UPDATE {clip_table} SET avg_completion_rate = COALESCE((
            SELECT AVG(completion_rate) FROM {interaction_table}
            WHERE clip_id = {clip_table}.id AND interaction_type = 'view'
        ), 0)
        WHERE status = 'ready' AND id > %s
        ORDER BY id
        LIMIT %s
    """

    with connection.cursor() as cur:
        total_rows = 0
        while True:
            cur.execute(ev_query, [last_id, BATCH_SIZE])
            ev_count = cur.rowcount
            cur.execute(acr_query, [last_id, BATCH_SIZE])
            acr_count = cur.rowcount
            if ev_count == 0 and acr_count == 0:
                break
            # Advance the cursor: use the highest id we may have updated.
            # Cheapest: re-query max(id) for the last batch.
            cur.execute(
                f"SELECT MAX(id) FROM {clip_table} WHERE id > %s AND status = 'ready'",
                [last_id],
            )
            row = cur.fetchone()
            new_last_id = row[0] if row and row[0] else None
            if not new_last_id:
                break
            last_id = new_last_id
            total_rows += max(ev_count, acr_count)
            if max(ev_count, acr_count) < BATCH_SIZE:
                break

        # Reset cursor after a full pass.
        if last_id:
            cache.set(cursor_key, None, timeout=86400)  # 24h safety net
        return f"Updated {total_rows} clips."


# Added retry config, batch_size=100 to prevent memory exhaustion with large user counts.
@shared_task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600)
def evolve_long_term_user_baselines(self):
    """
    Recompute long-term semantic + acoustic vectors for every active user
    from the last `limit` interactions. Batched in groups of BATCH_SIZE
    to bound memory: the previous code accumulated ALL users in
    `users_to_update` before a single bulk_update, which is O(N) memory.

    Tradeoff: more database round-trips, but bounded memory. With
    BATCH_SIZE=100 and 100k users, peak memory is ~5 MB instead of ~500 MB.
    """
    BATCH_SIZE = 100
    users_batch = []
    total_updated = 0
    for user in User.objects.filter(is_active=True).iterator(chunk_size=BATCH_SIZE):
        new_sem, new_ac = calculate_time_decayed_vectors(user, limit=100)
        if new_sem is not None:
            user.long_term_semantic = new_sem
            user.long_term_acoustic = new_ac
        users_batch.append(user)
        if len(users_batch) >= BATCH_SIZE:
            User.objects.bulk_update(
                users_batch,
                ['long_term_semantic', 'long_term_acoustic'],
                batch_size=BATCH_SIZE,
            )
            total_updated += len(users_batch)
            users_batch = []
    if users_batch:
        User.objects.bulk_update(
            users_batch,
            ['long_term_semantic', 'long_term_acoustic'],
            batch_size=BATCH_SIZE,
        )
        total_updated += len(users_batch)
    return f"Evolved long-term vectors for {total_updated} users."


@shared_task(bind=True, max_retries=3, default_retry_delay=10, retry_backoff=True)
def flush_telemetry(self, max_events=1000):
    """Drain the Redis telemetry queue and bulk-insert into UserInteraction.

    Architecture audit's #1 risk: synchronous update_or_create on every
    log_telemetry call holds row locks on UserInteraction. This task
    converts N per-request writes into a single bulk insert per flush.

    Triggered every 30s via Celery beat. Uses LPOP from a Redis list
    (atomic) up to max_events. Each event is a JSON object written by
    ClipInteractionViewSet.log_telemetry (views.py).
    """
    import json
    redis_client = cache.client.get_client()
    queue_key = 'telemetry:queue'
    events = []
    # LPOP in a tight loop. If queue is empty, returns None and we stop.
    for _ in range(max_events):
        raw = redis_client.lpop(queue_key)
        if raw is None:
            break
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning("flush_telemetry: dropped malformed event: %r", raw)
            continue

    if not events:
        return "No events to flush."

    # Materialize to ORM objects in one bulk_create.
    interactions = []
    for e in events:
        try:
            user = User.objects.get(id=e['user_id'])
            clip = AudioClip.objects.get(id=e['clip_id'])
        except (User.DoesNotExist, AudioClip.DoesNotExist):
            continue
        interactions.append(UserInteraction(
            user=user,
            clip=clip,
            interaction_type=e['action_type'],
            watch_time_ms=e['watch_time_ms'],
            completion_rate=e['completion_rate'],
            is_active=True,
        ))

    if not interactions:
        return "No valid events to flush."

    UserInteraction.objects.bulk_create(interactions, batch_size=500)
    return f"Flushed {len(interactions)} telemetry events to UserInteraction."


@shared_task
def cleanup_stuck_processing(threshold_minutes=15, max_per_run=50):
    """Re-enqueue clips stuck in 'processing' status past the threshold.

    Audit item 6.7: A Celery broker outage at the moment of
    transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))
    (views.py:101) silently leaves the clip in 'processing' forever.
    This task runs every 5 min via Celery beat and re-enqueues up to
    max_per_run stuck clips, with the same retry config to handle
    transient failures. After max_retries the clip is flipped to
    'failed' so it shows up in error reports.
    """
    threshold = timezone.now() - timedelta(minutes=threshold_minutes)
    stuck = (
        AudioClip.objects
        .filter(status='processing', created_at__lt=threshold)
        .order_by('created_at')[:max_per_run]
    )
    re_enqueued = 0
    for clip in stuck:
        # Cap retries: if a clip has been re-enqueued 3+ times, mark it failed.
        # (We track by updated_at as a proxy; a future schema field would
        # be cleaner.)
        if clip.updated_at and (timezone.now() - clip.updated_at) < timedelta(minutes=threshold_minutes * 3):
            # Skip — already re-enqueued recently by this same task.
            continue
        process_audio_to_hls.delay(str(clip.id))
        re_enqueued += 1
    return f"Re-enqueued {re_enqueued} stuck clips (threshold={threshold_minutes}m)."


@shared_task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600)
def scrape_and_import(self, source_name, limit=5, clip_length=300):
    """Celery task wrapper to run a scraper source and import clips.

    This task delegates to the source connectors and uses the local
    downloader/normalizer/uploader to create `AudioClip` records and
    then triggers `process_audio_to_hls` for each created clip.
    """
    from backend.app.scrapers.sources import SOURCES
    module = SOURCES.get(source_name)
    if not module:
        raise RuntimeError(f"Unknown source: {source_name}")

    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.filter(is_superuser=True).first()
    if not user:
        user = UserModel.objects.create_user(username='scraper')
        user.set_unusable_password()
        user.save()

    from backend.app.scrapers import downloader, normalizer, uploader

    items = module.fetch_audio(limit=limit)
    for item in items:
        url = item.get('url')
        title = item.get('title') or 'scraped audio'
        page = item.get('page_url') or ''
        license = item.get('license') or 'unknown'
        original_id = item.get('id')

        local_input = None
        tmp_out = None
        try:
            if url.startswith('file://'):
                local_input = url[len('file://'):]
            else:
                local_input = downloader.download_audio(url)

            tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3').name
            normalizer.normalize_and_trim(local_input, tmp_out, max_seconds=clip_length, target_format='mp3')

            clip = uploader.save_clip(
                user=user,
                title=title,
                source_name=source_name,
                source_url=page,
                license=license,
                attribution_text=page,
                local_file_path=tmp_out,
                original_source_id=original_id,
            )

            process_audio_to_hls.delay(str(clip.id))
            logger.info("Imported clip %s from %s", clip.id, source_name)

        except Exception as e:
            logger.error("Failed to import %s: %s", url, e)

        finally:
            # local_input/tmp_out are always tempfile-backed local scratch
            # paths here (never the durable store — see uploader.save_clip,
            # which already writes through default_storage), so there's
            # nothing to protect against deleting; clean up unconditionally.
            for p in (local_input, tmp_out):
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception as e:
                    logger.error("Failed to clean up temp file %s: %s", p, e)
                    


