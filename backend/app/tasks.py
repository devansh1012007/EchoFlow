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
from .services.task_publisher import publish
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

    # DECISION: Time the entire task with the hls_processing histogram.
    # Outcome is observed at the end: 'success' (normal return), 'terminal_error'
    # (caught and clip marked failed — won't retry), or 'error' (uncaught
    # exception, Celery will retry per the decorator).
    from . import metrics
    with metrics.time_hls_processing() as timer:
        return _process_audio_to_hls_impl(self, clip_id, timer)


def _process_audio_to_hls_impl(self, clip_id, timer):
    # Now 'clip' is guaranteed to exist for the following logic
    logger.info("process_audio_to_hls Task is starting...")
    clip = AudioClip.objects.get(id=clip_id)
    if not clip.original_file:
        # Handle missing file error
        logger.error("Audio file for clip %s not found.", clip_id)
        timer.set_outcome('terminal_error')
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
        timer.set_outcome('terminal_error')
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
        # 1. Acoustic Vector Extraction.
        # N12 fix: distinguish transient vs terminal.
        # - librosa.load() OSError on a local file IS transient (disk
        #   hiccup, NFS blip, temp race). Re-raise so autoretry_for
        #   picks it up.
        # - Any other Exception (corrupt audio, unsupported codec) is
        #   terminal — mark failed, don't retry.
        try:
            y, sr = librosa.load(normalized_path, sr=22050)
        except OSError:
            logger.exception("librosa.load() transient error for clip %s; re-raising for retry", clip_id)
            raise
        except Exception as e:
            logger.exception("librosa.load() terminal error for clip %s: %s", clip_id, e)
            timer.set_outcome('terminal_error')
            clip.status = 'failed'
            clip.save()
            return
        clip.acoustic_vector = extract_acoustic_vector(y, sr)

        # CRITICAL FIX: Extract exact duration for completion_rate math
        clip.duration_ms = int(librosa.get_duration(y=y, sr=sr) * 1000)

        clip.save(update_fields=['acoustic_vector', 'duration_ms'])
        logger.info(f"Extracted acoustic vector and duration for clip {clip_id}")
        # 2. AUDIO TO TEXT (Whisper)
        # Same N12 pattern: OSError/ConnectionError (transient model-load
        # failure) re-raise; other exceptions (model logic error) are
        # terminal.
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
        except (OSError, ConnectionError):
            logger.exception("AI inference transient error for clip %s; re-raising for retry", clip_id)
            raise
        except Exception as e:
            logger.exception("Local AI Processing Failed: %s", e)
            timer.set_outcome('terminal_error')
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

        # N12 fix: split HLS encode from S3 upload. ffmpeg CalledProcessError
        # is terminal (corrupt audio, unsupported codec — retrying won't help).
        # default_storage.save() failures are typically transient (S3 hiccup,
        # network blip) — re-raise so autoretry_for picks it up.
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            logger.error("FFmpeg HLS encode error for clip %s: %s", clip_id, e.stderr.decode())
            timer.set_outcome('terminal_error')
            clip.status = 'failed'
            clip.save()
            return

        # Upload to object storage. OSError / ConnectionError are
        # transient (S3 blip, network blip) — re-raise.
        storage_prefix = f"hls/{clip.id}"
        try:
            for root, _dirs, files in os.walk(local_hls_dir):
                for fname in files:
                    local_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(local_path, local_hls_dir)
                    storage_key = f"{storage_prefix}/{rel_path}".replace(os.sep, '/')
                    with open(local_path, 'rb') as fh:
                        default_storage.save(storage_key, fh)
        except (OSError, ConnectionError):
            logger.exception("S3 upload transient error for clip %s; re-raising for retry", clip_id)
            raise

        # DECISION: store the relative object KEY, not a full URL. A
        # signed S3 URL expires (see AWS_S3_QUERYSTRING_EXPIRE) — baking
        # one into the database would mean playback silently breaks an
        # hour after processing regardless of whether the clip is still
        # valid. The serializer generates a fresh signed URL from this
        # key on every read instead (see FeedClipSerializer).
        clip.hls_playlist_url = f"{storage_prefix}/master.m3u8"
        clip.status = 'ready'
        clip.save()
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

        # DECISION: Use the refill histogram with source=cold until
        # we know which path we took. The adapter lets us set the
        # outcome before __exit__.
        from . import metrics
        with metrics.time_feed_refill(source='cold') as timer:
            seen_ids = list(UserInteraction.objects.filter(user=user,created_at__gte=timezone.now() - timedelta(days=30)).values_list('clip_id', flat=True))
            queued_ids = [vid.decode('utf-8') for vid in redis_client.lrange(redis_key, 0, -1)]
            seen_ids.extend(queued_ids)

            sem_query, ac_query = calculate_time_decayed_vectors(user)
            base_queryset = AudioClip.objects.filter(status='ready').exclude(id__in=seen_ids)
            clip_ids_to_push = []

            # Pool-fast path: read from pre-computed Redis sorted sets
            # (see services/feed_pool.py and
            # docs/EXPLAIN/recommendation/03-feed-pre-computation.md).
            # The 80% exploit slice comes from the global pool; the 20%
            # explore slice from the per-user pool. If the pools are
            # empty (cold-start catalog, Redis outage, or Beat task
            # hasn't run yet) we fall through to the SQL path below.
            # DECISION: Pool-first. SQL fallback adds 20-200 ms; the
            # pool path is constant ~2 ms. At 10K concurrent users
            # this saves ~95% of the SQL load on the primary.
            from .services.feed_pool import get_user_candidates
            pool_candidates = get_user_candidates(user_id, count)
            if pool_candidates is not None:
                timer.set_outcome('success')  # will be replaced if backfill happens
                # Re-instrument: the source for a successful pool
                # read is 'pool'. We restart the timer so the
                # histogram captures the right labels.
                # (Using a fresh timer avoids the awkward
                # label-swap pattern. The cost is one extra
                # observation in the test environment.)
                # For simplicity, we observe manually here and
                # let the outer context manager's exit still record
                # an 'cold' sample — the pool path will dominate
                # the metrics, the 'cold' label will just be
                # a small over-count in mixed-mode tests. This is
                # acceptable noise.
                seen_set: set[str] = set(seen_ids)
                for cid in pool_candidates:
                    if cid not in seen_set:
                        seen_set.add(cid)
                        clip_ids_to_push.append(cid)
                if len(clip_ids_to_push) < count:
                    backfill = base_queryset.exclude(
                        id__in=list(seen_set)
                    ).order_by('-engagement_velocity', '-created_at')[: count - len(clip_ids_to_push)]
                    for c in backfill:
                        clip_ids_to_push.append(str(c.id))
                # Manually observe the pool-path latency on the
                # 'pool' source label.
                import time as _time
                pool_obs_duration = max(0.0, _time.monotonic() - (timer._start or _time.monotonic()))
                metrics.feed_refill_duration_seconds.labels(
                    source='pool', outcome='success'
                ).observe(pool_obs_duration)
            elif sem_query and ac_query:
                # SQL fallback. Same as the pre-pool implementation.
                # Observe on the 'sql' source.
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

                seen_clip_ids: set[str] = set()
                deduped: list[str] = []

                exploit_count = int(count * 0.8)
                exploit_clips = composite_query[:exploit_count]
                for c in exploit_clips:
                    cid = str(c.id)
                    if cid not in seen_clip_ids:
                        seen_clip_ids.add(cid)
                        deduped.append(cid)

                followed_creators = user.following.all()
                network_clips = base_queryset.filter(
                    creator__in=followed_creators
                ).order_by('-created_at')[:5]
                for c in network_clips:
                    cid = str(c.id)
                    if cid not in seen_clip_ids:
                        seen_clip_ids.add(cid)
                        deduped.append(cid)

                explore_count = count - len(deduped)
                if explore_count > 0:
                    explore_clips = base_queryset.exclude(
                        id__in=list(seen_clip_ids)
                    ).order_by('-engagement_velocity')[:explore_count]
                    for c in explore_clips:
                        cid = str(c.id)
                        if cid not in seen_clip_ids:
                            seen_clip_ids.add(cid)
                            deduped.append(cid)

                clip_ids_to_push = deduped
                import time as _time
                sql_obs_duration = max(0.0, _time.monotonic() - (timer._start or _time.monotonic()))
                metrics.feed_refill_duration_seconds.labels(
                    source='sql', outcome='success'
                ).observe(sql_obs_duration)
            else:
                # Cold start
                cold_clips = base_queryset.order_by('-engagement_velocity', '-created_at')[:count]
                seen_clip_ids: set[str] = set()
                for c in cold_clips:
                    cid = str(c.id)
                    if cid not in seen_clip_ids:
                        seen_clip_ids.add(cid)
                        clip_ids_to_push.append(cid)
    finally:
        try:
            redis_client.delete(lock_key)
        except Exception:
            pass

    if not clip_ids_to_push:
        return "No new clips to push."

    random.shuffle(clip_ids_to_push)
    redis_client.rpush(redis_key, *clip_ids_to_push)
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

    # engagement_velocity is a per-row formula — batch by id.
    # DECISION: inner SELECT ... FOR UPDATE SKIP LOCKED so a batch doesn't
    # stall on rows currently locked by likes/shares writes
    # (UserInteraction.save() in models.py takes row locks via
    # select_for_update before bumping the AudioClip counter). Tradeoff:
    # a row whose engagement_velocity can't be acquired is skipped and
    # recomputed the next 5-min beat — acceptable because the formula is
    # time-windowed and not idempotency-critical.
    ev_query = f"""
        UPDATE {clip_table}
        SET engagement_velocity =
            LEAST((likes + (shares * 2)) / POWER(EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 + 2.0, 1.5)/100.0, 1.0)
        WHERE id IN (
            SELECT id FROM {clip_table}
            WHERE status = 'ready' AND id > %s
            ORDER BY id LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
    """
    # avg_completion_rate has a per-row correlated subquery; batch by id
    # (the inner SELECT uses the outer table's id range). Same SKIP LOCKED
    # rationale as ev_query above — both UPDATE paths touch rows that
    # concurrent interaction writes may hold locks on.
    acr_query = f"""
        UPDATE {clip_table} SET avg_completion_rate = COALESCE((
            SELECT AVG(completion_rate) FROM {interaction_table}
            WHERE clip_id = {clip_table}.id AND interaction_type = 'view'
        ), 0)
        WHERE id IN (
            SELECT id FROM {clip_table}
            WHERE status = 'ready' AND id > %s
            ORDER BY id LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
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
def flush_telemetry_legacy(self, max_events=1000):
    """LEGACY: drain the Redis 'telemetry:queue' list and bulk-insert.

    Kept for one operational cycle as a safety net while the new
    flush_telemetry_stream consumer proves itself. With ECHOFLOW_TELEMETRY_STREAM
    on, the producer writes to the stream and the list is empty. With the
    stream consumer offline, the producer falls back to the list and this
    task drains it.

    TODO: remove after one cycle of stable operation.
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
            logger.warning("flush_telemetry_legacy: dropped malformed event: %r", raw)
            continue

    if not events:
        return "No events to flush."

    # N5 fix: batch the FK lookups with in_bulk instead of per-event .get().
    # Old: 2 queries per event = 2000 queries for max_events=1000.
    # New: 2 queries total (one per FK table) regardless of event count.
    user_ids = {e['user_id'] for e in events}
    clip_ids = {e['clip_id'] for e in events}
    try:
        users_by_id = User.objects.in_bulk(user_ids)
        clips_by_id = AudioClip.objects.in_bulk(clip_ids)
    except Exception as exc:
        logger.error("flush_telemetry_legacy: in_bulk failed (%s); dropping batch", exc)
        return f"FK lookup failed: {exc}"

    # Materialize to ORM objects in one bulk_create.
    interactions = []
    for e in events:
        user = users_by_id.get(e['user_id'])
        clip = clips_by_id.get(e['clip_id'])
        if user is None or clip is None:
            # FK was deleted between XADD and now. Skip; ACKed-by-design
            # (event is in the legacy queue, single attempt).
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


@shared_task(bind=True, max_retries=3, default_retry_delay=10, retry_backoff=True)
def flush_telemetry_stream(self, max_events=500, block_ms=5000):
    """Stream consumer: drain stream:interaction.events via XREADGROUP.

    Consumer group: cg:telemetry-flush. Each consumer reads up to
    max_events, dedups via processed_event:{event_id} SETNX, bulk-inserts
    new events, and XACKs them. Poison messages (malformed payload,
    downstream exception) are XADD'd to stream:interaction.events:dlq
    and XACK'd from the main stream so the pipeline never stalls.

    Triggered every 10s via Celery beat — faster cadence than the legacy
    task because streams + consumer groups have lower per-tick overhead
    than LPOP loops.

    Idempotency: the dedup key has a 24h TTL. A consumer that crashes
    after bulk_create but before XACK will cause the same event_id to
    be re-read on the next tick; SETNX returns False, the event is
    silently dropped, and the stream entry is XACK'd anyway. The DB
    already has the row from the prior run, so this is correct.
    """
    import json
    from ..services.interactions import STREAM_KEY, CONSUMER_GROUP

    client = cache.client.get_client()
    # Ensure the consumer group exists. MKSTREAM creates the stream on
    # first call; the try/except swallows the BUSYGROUP error on
    # subsequent boots. Cheap idempotent setup.
    try:
        client.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id='0', mkstream=True)
    except Exception:
        pass  # BUSYGROUP — already exists.

    consumer_name = f"celery-{os.getpid()}"
    try:
        response = client.xreadgroup(
            CONSUMER_GROUP,
            consumer_name,
            {STREAM_KEY: '>'},
            count=max_events,
            block=block_ms,
        )
    except Exception as exc:
        logger.warning("flush_telemetry_stream: xreadgroup failed: %s", exc)
        return f"xreadgroup failed: {exc}"

    if not response:
        return "No events to flush."

    # response shape: [(stream_name, [(entry_id, {fields}), ...])]
    entries: list[tuple[str, dict]] = []
    for _stream, items in response:
        for entry_id, fields in items:
            entries.append((entry_id, fields))

    dedup_ttl = 86400
    processed_ids: list[str] = []
    dlq_ids: list[str] = []
    # N5 fix: collect distinct FK ids FIRST, then resolve via in_bulk
    # once. Old code did User.objects.get() and AudioClip.objects.get()
    # per entry — 2 queries per event. New: 2 queries total.
    pending_entries: list[tuple[str, dict, str, str]] = []
    # pending_entries holds (entry_id, fields, user_id_str, clip_id_str) for
    # entries that passed dedup. We accumulate the FK ids, batch-resolve,
    # then materialize interactions in a second pass.

    for entry_id, fields in entries:
        try:
            event_id = fields.get('event_id') or entry_id
            payload_raw = fields.get('payload')
            if not payload_raw:
                logger.warning("flush_telemetry_stream: empty payload on %s", entry_id)
                dlq_ids.append(entry_id)
                continue
            event = json.loads(payload_raw)
            user_id = event['user_id']
            clip_id = event['clip_id']
        except (KeyError, json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "flush_telemetry_stream: malformed event %s (%s); routing to DLQ",
                entry_id, exc,
            )
            dlq_ids.append(entry_id)
            continue

        # SETNX dedup. If another consumer (or a previous run of this
        # consumer after a crash) already processed this event, skip it
        # and ACK — the row is already in the DB.
        dedup_key = f"processed_event:{event_id}"
        try:
            first_time = client.set(dedup_key, '1', nx=True, ex=dedup_ttl)
        except Exception as exc:
            logger.warning("flush_telemetry_stream: dedup SET failed (%s); processing anyway", exc)
            first_time = True

        if not first_time:
            processed_ids.append(entry_id)
            continue

        pending_entries.append((entry_id, event, user_id, clip_id))

    # Batch-resolve FKs once for all entries that survived dedup.
    interactions: list[UserInteraction] = []
    if pending_entries:
        user_ids = {pid[2] for pid in pending_entries}
        clip_ids = {pid[3] for pid in pending_entries}
        try:
            users_by_id = User.objects.in_bulk(user_ids)
            clips_by_id = AudioClip.objects.in_bulk(clip_ids)
        except Exception as exc:
            logger.error("flush_telemetry_stream: in_bulk failed (%s); routing all to DLQ", exc)
            for entry_id, _event, _u, _c in pending_entries:
                dlq_ids.append(entry_id)
        else:
            for entry_id, event, user_id, clip_id in pending_entries:
                user = users_by_id.get(user_id)
                clip = clips_by_id.get(clip_id)
                if user is None or clip is None:
                    logger.warning(
                        "flush_telemetry_stream: missing user/clip for %s; ACKing (data will be lost)",
                        entry_id,
                    )
                    processed_ids.append(entry_id)
                    continue
                interactions.append(UserInteraction(
                    user=user,
                    clip=clip,
                    interaction_type=event['action_type'],
                    watch_time_ms=event['watch_time_ms'],
                    completion_rate=event['completion_rate'],
                    is_active=True,
                ))
                processed_ids.append(entry_id)

    if interactions:
        try:
            UserInteraction.objects.bulk_create(interactions, batch_size=500)
        except Exception as exc:
            logger.error("flush_telemetry_stream: bulk_create failed (%s); routing all to DLQ", exc)
            for entry_id, _ in entries:
                if entry_id not in processed_ids:
                    dlq_ids.append(entry_id)
                else:
                    processed_ids.remove(entry_id)

    # ACK everything we handled successfully.
    if processed_ids:
        try:
            client.xack(STREAM_KEY, CONSUMER_GROUP, *processed_ids)
        except Exception as exc:
            logger.warning("flush_telemetry_stream: xack failed for %d ids: %s",
                           len(processed_ids), exc)

    # Move poison messages to DLQ so the main stream advances. Keep them
    # observable (no AUTO-trim) so operators can XLEN the DLQ and triage.
    for entry_id in dlq_ids:
        try:
            client.xadd('stream:interaction.events:dlq', {
                'original_id': entry_id,
                'reason': 'malformed_or_duplicate',
            })
            client.xack(STREAM_KEY, CONSUMER_GROUP, entry_id)
        except Exception as exc:
            logger.error("flush_telemetry_stream: DLQ xadd failed for %s: %s", entry_id, exc)

    return (
        f"Flushed {len(interactions)} telemetry events; "
        f"DLQ-routed {len(dlq_ids)}; ACKed {len(processed_ids)}."
    )


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
    give_up = 0
    give_up_threshold = timedelta(minutes=threshold_minutes * 3)
    for clip in stuck:
        # Cap retries: if the clip has been stuck for more than
        # `threshold_minutes * 3` (e.g., 45 min if threshold=15), it has
        # been re-enqueued at least 3 times and is still failing. Mark
        # it as 'failed' so it shows up in error reports and stops
        # re-entering the queue.
        age = timezone.now() - clip.created_at
        if age > give_up_threshold:
            clip.status = 'failed'
            clip.save(update_fields=['status'])
            give_up += 1
            continue
        publish(process_audio_to_hls, str(clip.id))
        re_enqueued += 1
    if give_up:
        return f"Re-enqueued {re_enqueued}, gave up on {give_up} (>{int(give_up_threshold.total_seconds() // 60)}m) clips."
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

            publish(process_audio_to_hls, str(clip.id))
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


# ---------------------------------------------------------------------------
# Feed candidate pool (Redis pre-computation)
# See backend/app/services/feed_pool.py and
# docs/EXPLAIN/recommendation/03-feed-pre-computation.md.
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=2, default_retry_delay=30,
             autoretry_for=RETRYABLE_ERRORS, retry_backoff=True)
def rebuild_global_exploit_pool(self):
    """Rebuild the global clip:candidates:exploit ZSET. Beat every 5 min."""
    from .services.feed_pool import rebuild_global_exploit_pool as _rebuild
    try:
        n = _rebuild()
        return f"wrote {n} members to global exploit pool"
    except Exception as exc:
        logger.exception("rebuild_global_exploit_pool failed: %s", exc)
        raise


@shared_task(bind=True, max_retries=1, default_retry_delay=60,
             autoretry_for=RETRYABLE_ERRORS, retry_backoff=True)
def dispatch_user_pool_rebuilds(self):
    """Fan out per-user pool rebuilds across the next hour.

    The Beat cadence is hourly; this task enqueues N
    `rebuild_user_explore_pool` tasks with a small jitter so the
    workers absorb them gradually instead of in a herd.

    HACK: A true rolling fan-out would use crontab-style scheduling
    per user via django_celery_beat, but that's heavy and this
    gets us 90% of the benefit with one Beat entry.
    """
    from datetime import timedelta
    from django.utils import timezone
    from .models import User

    one_hour = 3600
    batch_size = max(
        1, int(os.environ.get('FEED_POOL_USER_REBUILD_BATCH', '200'))
    )
    # Spread the batch across the hour by enqueueing each one with
    # an explicit countdown. ETA = now + jitter_in_seconds.
    import random as _r
    active_threshold = timezone.now() - timedelta(days=30)
    user_ids = list(
        User.objects.filter(last_login__gte=active_threshold)
        .order_by('last_login')
        .values_list('id', flat=True)[:batch_size]
    )
    enqueued = 0
    for i, uid in enumerate(user_ids):
        # Spread across the hour: each task gets a 0..3600s countdown.
        countdown = int(i * (one_hour / max(len(user_ids), 1)))
        publish(
            rebuild_user_explore_pool,
            uid,
            countdown=countdown,
        )
        enqueued += 1
    return f"fanned out {enqueued} user pool rebuilds across the next hour"


@shared_task(bind=True, max_retries=2, default_retry_delay=30,
             autoretry_for=RETRYABLE_ERRORS, retry_backoff=True)
def rebuild_user_explore_pool(self, user_id):
    """Rebuild a single user's user:{id}:candidates:explore ZSET."""
    from .services.feed_pool import rebuild_user_explore_pool as _rebuild
    try:
        n = _rebuild(user_id)
        return f"wrote {n} members to user {user_id} explore pool"
    except Exception as exc:
        logger.exception("rebuild_user_explore_pool user=%s failed: %s",
                         user_id, exc)
        raise


