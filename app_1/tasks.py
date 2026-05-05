import os
import subprocess
import random
import json
import math
import numpy as np
import logging
import librosa
from celery import shared_task
from django.conf import settings
from django.db import connection
from django.db.models import F, FloatField, ExpressionWrapper, Avg
from django.db.models.functions import Now
from django.utils import timezone
from datetime import timedelta
from django.core.cache import cache
from pgvector.django import CosineDistance
from openai import OpenAI
from .models import AudioClip, UserInteraction, User
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)
#from faster_whisper import WhisperModel
#from sentence_transformers import SentenceTransformer
#from keybert import KeyBERT

#whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
#embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
#kw_model = KeyBERT()

whisper_model = None
embedding_model = None
kw_model = None

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

logger = logging.getLogger(__name__)


def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        except Exception as e:
            logger.exception("Failed to initialize WhisperModel: %s", e)
            raise
    return whisper_model


def get_embedding_model():
    global embedding_model
    if embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        except Exception as e:
            logger.exception("Failed to initialize embedding model: %s", e)
            raise
    return embedding_model


def get_kw_model():
    global kw_model
    if kw_model is None:
        try:
            from keybert import KeyBERT
            kw_model = KeyBERT()
        except Exception as e:
            logger.exception("Failed to initialize KeyBERT: %s", e)
            raise
    return kw_model

def get_openai_client():
    """Create an OpenAI client only when the task is executed.

    This avoids import-time failures during Django management commands when the
    OpenAI API key is not configured in the environment.
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set. OpenAI calls require this environment variable.")
    return OpenAI(api_key=OPENAI_API_KEY)


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


@shared_task
def process_audio_to_hls(clip_id):

    # Now 'clip' is guaranteed to exist for the following logic
    logger.info("process_audio_to_hls Task is starting...")    
    clip = AudioClip.objects.get(id=clip_id)
    input_file_path = clip.original_file.path
    if not clip.original_file:
        # Handle missing file error
        print(f"Error: Audio file for clip {clip_id} not found.")
        clip.status = 'failed'
        clip.save()
        return
    # 1. Acoustic Vector Extraction
    y, sr = librosa.load(input_file_path, sr=22050)
    clip.acoustic_vector = extract_acoustic_vector(y, sr)
    
    # CRITICAL FIX: Extract exact duration for completion_rate math
    clip.duration_ms = int(librosa.get_duration(y=y, sr=sr) * 1000)

    clip.save(update_fields=['acoustic_vector', 'duration_ms'])
    logger.info(f"Extracted acoustic vector and duration for clip {clip_id}")
    # 2. AUDIO TO TEXT (Whisper)
    try:
        # Lazy-init models to avoid startup cost during management commands
        model = get_whisper_model()
        segments, info = model.transcribe(input_file_path, beam_size=5)
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
    
    output_dir = os.path.join(settings.MEDIA_ROOT, 'hls', str(clip.id))
    os.makedirs(output_dir, exist_ok=True)
    
    command = [
        'ffmpeg', '-y', '-i', input_file_path,
        '-c:a', 'aac', '-ar', '44100',
        '-map', '0:a', '-map', '0:a', '-map', '0:a',
        '-b:a:0', '192k', '-b:a:1', '128k', '-b:a:2', '64k',
        '-f', 'hls', '-hls_time', '4', '-hls_playlist_type', 'vod',
        '-var_stream_map', 'a:0,agroup:audio,default:yes a:1,agroup:audio a:2,agroup:audio',
        '-master_pl_name', 'master.m3u8',
        os.path.join(output_dir, '%v', 'index.m3u8')
    ]

    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        clip.hls_playlist_url = f"/media/hls/{clip.id}/master.m3u8"
        clip.status = 'ready'
        clip.save()
    except subprocess.CalledProcessError as e:
        clip.status = 'failed'
        clip.save()
        print(f"FFmpeg Error: {e.stderr.decode()}")
    
    # for when i will have money for API
    '''try:
        client = get_openai_client()
        with open(input_file_path, "rb") as audio_file:
            transcript_response = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file
            )
        transcript_text = transcript_response.text
        # 2. SEMANTIC VECTOR EXTRACTION (OpenAI Text Embeddings)
        clip.semantic_vector = client.embeddings.create(
        input=transcript_text, model="text-embedding-3-small"
    ).data[0].embedding
        # 3. AUTOMATED TAGGING (LLM Extraction)
        # We ask a lightweight model to categorize the transcript
        # add pydentic model for validation in production
        tag_response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Extract exactly 3 lower-case, single-word genre tags from this text. Return ONLY a JSON array of strings."},
                {"role": "user", "content": transcript_text}
            ]
        )
        clip.tags = json.loads(tag_response.choices[0].message.content)
    

    except Exception as e:
        print(f"AI Processing Failed: {e}")
        # In production, you would log this to Sentry and potentially retry
    
    # Create a unique output directory for this clip
    output_dir = os.path.join(settings.MEDIA_ROOT, 'hls', str(clip.id))
    os.makedirs(output_dir, exist_ok=True)
    
    # The Master Playlist path
    master_playlist_path = os.path.join(output_dir, 'master.m3u8')

    # FFmpeg command for Audio-Only ABR
    # We create 3 variants: 192k, 128k, and 64k.
    # -hls_time 4: Chops the audio into 4-second segments
    command = [
        'ffmpeg', '-y', '-i', input_file_path,
        
        # Audio formatting (AAC is standard for HLS)
        '-c:a', 'aac', '-ar', '44100',
        
        # Map the input to 3 different outputs
        '-map', '0:a', '-map', '0:a', '-map', '0:a',
        
        # Set the bitrates for each mapping
        '-b:a:0', '192k',
        '-b:a:1', '128k',
        '-b:a:2', '64k',
        
        # HLS Configuration
        '-f', 'hls',
        '-hls_time', '4', # 4 second chunks
        '-hls_playlist_type', 'vod',
        
        # Create the variant sub-playlists
        '-var_stream_map', 'a:0,agroup:audio,default:yes a:1,agroup:audio a:2,agroup:audio',
        '-master_pl_name', 'master.m3u8',
        
        # Output paths (creates the folders dynamically)
        os.path.join(output_dir, '%v', 'index.m3u8')
    ]

    try:
        # Run the FFmpeg command
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Update the database with the new URL and status
        # Note: In production, you'd upload this directory to AWS S3 and save the S3 URL.
        clip.hls_playlist_url = f"/media/hls/{clip.id}/master.m3u8"
        clip.status = 'ready'
        clip.save()

    except subprocess.CalledProcessError as e:
        clip.status = 'failed'
        clip.save()
        print(f"FFmpeg Error: {e.stderr.decode()}")
    '''




def calculate_dynamic_user_vector(user_id):
    """
    Calculates the user's current mood vector based on recent interactions.
    """
    # Fetch the 15 most recent positive interactions
    recent_positive_interactions = UserInteraction.objects.filter(
        user_id=user_id,
        interaction_type__in=['like', 'share']
    ).select_related('clip').order_by('-created_at')[:15]
    
    if not recent_positive_interactions:
        return None

    # Extract the vectors
    vectors = [
        np.array(interaction.clip.semantic_vector)
        for interaction in recent_positive_interactions 
        if interaction.clip.semantic_vector is not None
    ]
    
    if not vectors:
        return None
        
    # Calculate the centroid (average) of these vectors
    # This represents their exact "vibe" right now
    centroid_vector = np.mean(vectors, axis=0)
    
    # Normalize the vector to maintain standard cosine similarity geometry
    norm = np.linalg.norm(centroid_vector)
    if norm > 0:
        centroid_vector = centroid_vector / norm
        
    return centroid_vector.tolist()

def calculate_blended_query_vectors(user):
    # 1. Fetch interactions, applying time decay in Python
    now = timezone.now()
    cutoff = now - timedelta(days=7) # Only consider last 7 days for context
    
    recent_interactions = UserInteraction.objects.filter(
        user=user, 
        interaction_type__in=['like', 'share', 'view'],
        is_active=True,
        updated_at__gte=cutoff
    ).select_related('clip')
    
    if not recent_interactions:
        return user.long_term_semantic, user.long_term_acoustic

    sem_vectors = []
    ac_vectors = []
    weights = []

    for interaction in recent_interactions:
        hours_since = max((now - interaction.updated_at).total_seconds() / 3600, 0.1)
        # Time decay logic: 1 / (1 + log(hours))
        weight = 1.0 / (1.0 + math.log(hours_since + 1))
        
        # Boost weight by completion rate if available
        if interaction.completion_rate:
            weight *= (interaction.completion_rate + 0.5)

        if interaction.clip.semantic_vector:
            sem_vectors.append(np.array(interaction.clip.semantic_vector) * weight)
        if interaction.clip.acoustic_vector:
            ac_vectors.append(np.array(interaction.clip.acoustic_vector) * weight)
        weights.append(weight)

    total_weight = sum(weights) if weights else 1
    context_sem = np.sum(sem_vectors, axis=0) / total_weight if sem_vectors else None
    context_ac = np.sum(ac_vectors, axis=0) / total_weight if ac_vectors else None

    ALPHA = 0.75 # 75% short term mood, 25% long term baseline
    
    if user.long_term_semantic and context_sem is not None:
        final_sem = (ALPHA * context_sem) + ((1 - ALPHA) * np.array(user.long_term_semantic))
    else:
        final_sem = context_sem or user.long_term_semantic

    if user.long_term_acoustic and context_ac is not None:
        final_ac = (ALPHA * context_ac) + ((1 - ALPHA) * np.array(user.long_term_acoustic))
    else:
        final_ac = context_ac or user.long_term_acoustic
        
    return (
        final_sem.tolist() if final_sem is not None else None, 
        final_ac.tolist() if final_ac is not None else None
    )

@shared_task
def refill_user_feed(user_id, count=50):
    user = User.objects.get(id=user_id)
    redis_key = f"user_feed:{user_id}"
    redis_client = cache.client.get_client()

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

    if not clip_ids_to_push:
        return "No new clips."

    random.shuffle(clip_ids_to_push)
    redis_client.rpush(redis_key, *clip_ids_to_push)

    return f"Added {len(clip_ids_to_push)} composite-ranked clips."

def calculate_time_decayed_vectors(user, limit=50):
    recent_interactions = UserInteraction.objects.filter(
        user=user
    ).select_related('clip').order_by('-created_at')[:limit]
    
    if not recent_interactions:
        return user.long_term_semantic, user.long_term_acoustic

    now = timezone.now()
    sem_vectors, ac_vectors, weights = [], [], []

    for interaction in recent_interactions:
        if not interaction.clip.semantic_vector:
            continue

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
        if ac_vectors:
            ac_vectors.append(np.array(interaction.clip.acoustic_vector) * final_weight)
        if sem_vectors:
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
    if user.long_term_semantic:
        final_sem = (ALPHA * weighted_sem) + ((1 - ALPHA) * np.array(user.long_term_semantic))
        final_ac = (ALPHA * weighted_ac) + ((1 - ALPHA) * np.array(user.long_term_acoustic))
    else:
        final_sem, final_ac = weighted_sem, weighted_ac

    final_sem = final_sem / np.linalg.norm(final_sem)
    final_ac = final_ac / np.linalg.norm(final_ac)

    return final_sem.tolist(), final_ac.tolist()


@shared_task
def update_global_metrics():
    """
    Run every 10 minutes via Celery Beat to recalculate global clip performance.
    Formula punishes older videos that stop accumulating engagement.
    """
    # convert to orm
    AudioClip._meta.db_table ######## # Just to ensure the table name is correct for raw SQL, but we will convert to ORM below
    query = """
    UPDATE app_1_audioclip 
    SET engagement_velocity = 
        LEAST((likes + (shares * 2)) / POWER(EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 + 2.0, 1.5)/100.0, 1.0) -- Normalize to 0-1 range
    WHERE status = 'ready';
    """
    UserInteraction._meta.db_table ######### just to ensure the table name is correct for raw SQL, but we will convert to ORM below
    # convert to orm 

    query2 = """    
    UPDATE app_1_audioclip SET avg_completion_rate = (
    SELECT AVG(completion_rate) FROM app_1_userinteraction
    WHERE clip_id = app_1_audioclip.id AND interaction_type = 'view'
) WHERE status = 'ready';
    """
    

    with connection.cursor() as cursor:
        cursor.execute(query)
        cursor.execute(query2)

@shared_task
def evolve_long_term_user_baselines():
    """
    Run every 24 hours at 3:00 AM.
    Prevents the user's long-term vector from stagnating indefinitely.
    """
    users_to_update = []
    for user in User.objects.filter(is_active=True).iterator(chunk_size=100):
        new_sem, new_ac = calculate_time_decayed_vectors(user, limit=500)
        if new_sem is not None:            
            user.long_term_semantic = new_sem 
            user.long_term_acoustic = new_ac
        users_to_update.append(user)
    User.objects.bulk_update(users_to_update, ['long_term_semantic', 'long_term_acoustic'], batch_size=100)


@shared_task
def scrape_and_import(source_name, limit=5, clip_length=300):
    """Celery task wrapper to run a scraper source and import clips.

    This task delegates to the source connectors and uses the local
    downloader/normalizer/uploader to create `AudioClip` records and
    then triggers `process_audio_to_hls` for each created clip.
    """
    from app_1.scrapers.sources import SOURCES
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

    from app_1.scrapers import downloader, normalizer, uploader
    import tempfile

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
            print(f'Imported clip {clip.id} from {source_name}')

        except Exception as e:
            print(f'Failed to import {url}: {e}')

        finally:
            for p in (local_input, tmp_out):
                try:
                    if p and os.path.exists(p) and not p.startswith(settings.MEDIA_ROOT):
                        os.remove(p)
                except Exception:
                    pass


