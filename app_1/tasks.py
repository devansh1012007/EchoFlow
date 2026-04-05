import os
import subprocess
from celery import shared_task
from django.conf import settings
from .models import AudioClip
import numpy as np
import subprocess
from celery import shared_task
from django.conf import settings
from openai import OpenAI
import librosa
import json
from .models import AudioClip
from .models import UserInteraction, User
import math
import numpy as np
from datetime import timedelta
from celery import shared_task
from django.utils import timezone
from django.db.models import F, FloatField, ExpressionWrapper
from django.db.models.functions import ExtractEpoch, Now
from django.core.cache import cache
from pgvector.django import CosineDistance
from .models import AudioClip, UserInteraction, User



client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def extract_acoustic_vector(file_path):
    """
    Extracts exactly 128 acoustic features representing the "vibe" of the audio.
    """
    # Load audio (downsample to 22050Hz for faster processing)
    y, sr = librosa.load(file_path, sr=22050)
    
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
    clip = AudioClip.objects.get(id=clip_id)
    input_file_path = clip.original_file.path
    # 1. Acoustic Vector Extraction (Local CPU)
    clip.acoustic_vector = extract_acoustic_vector(input_file_path)
    # 1. AUDIO TO TEXT (Whisper)
    try:
        with open(input_file_path, "rb") as audio_file:
            transcript_response = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file
            )
        transcript_text = transcript_response.text
        clip.semantic_vector = client.embeddings.create(
        input=transcript_text, model="text-embedding-3-small"
    ).data[0].embedding
        # 2. TEXT TO VECTOR (Embeddings)
        # text-embedding-3-small generates exactly 1536 dimensions
        embedding_response = client.embeddings.create(
            input=transcript_text,
            model="text-embedding-3-small"
        )
        clip.vibe_vector = embedding_response.data[0].embedding
        
        # 3. AUTOMATED TAGGING (LLM Extraction)
        # We ask a lightweight model to categorize the transcript
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


# app_1/tasks.py
from celery import shared_task
from django.core.cache import cache
from .models import AudioClip, UserInteraction
from pgvector.django import CosineDistance
from django.core.cache import cache

from django.db.models import Avg
import numpy as np

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
        np.array(interaction.clip.vibe_vector) 
        for interaction in recent_positive_interactions 
        if interaction.clip.vibe_vector is not None
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

    seen_ids = list(UserInteraction.objects.filter(user=user).values_list('clip_id', flat=True))
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
                (F('vector_similarity') * 0.40) +
                (F('avg_completion_rate') * 0.30) +
                (F('engagement_velocity') * 0.20),
                output_field=FloatField()
            )
        ).order_by('-composite_score')

        # 80% EXPLOIT: Serve highest scoring algorithmic matches
        exploit_count = int(count * 0.8)
        exploit_clips = composite_query[:exploit_count]
        clip_ids_to_push.extend([str(c.id) for c in exploit_clips])

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

@shared_task
def update_long_term_vectors():
    """Run this periodically (e.g., daily) via celery-beat to prevent vector stagnation."""
    active_users = User.objects.filter(
        userinteraction__created_at__gte=timezone.now() - timedelta(days=1)
    ).distinct()

    for user in active_users:
        interactions = UserInteraction.objects.filter(
            user=user, 
            interaction_type__in=['like', 'share'], 
            is_active=True
        ).select_related('clip').order_by('-updated_at')[:100]

        if not interactions:
            continue
            
        sem_vectors = [np.array(i.clip.semantic_vector) for i in interactions if i.clip.semantic_vector]
        ac_vectors = [np.array(i.clip.acoustic_vector) for i in interactions if i.clip.acoustic_vector]

        if sem_vectors:
            user.long_term_semantic = np.mean(sem_vectors, axis=0).tolist()
        if ac_vectors:
            user.long_term_acoustic = np.mean(ac_vectors, axis=0).tolist()
        
        user.save(update_fields=['long_term_semantic', 'long_term_acoustic'])

import math
import random
import numpy as np
from django.utils import timezone
from django.db.models import F, ExpressionWrapper, FloatField
from pgvector.django import CosineDistance
from celery import shared_task
from django.core.cache import cache
from .models import User, AudioClip, UserInteraction

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

        sem_vectors.append(np.array(interaction.clip.semantic_vector) * final_weight)
        ac_vectors.append(np.array(interaction.clip.acoustic_vector) * final_weight)
        weights.append(final_weight)

    sum_weights = sum(weights)
    if sum_weights == 0:
        return user.long_term_semantic, user.long_term_acoustic

    weighted_sem = np.sum(sem_vectors, axis=0) / sum_weights
    weighted_ac = np.sum(ac_vectors, axis=0) / sum_weights

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
    from django.db import connection
    
    query = """
    UPDATE app_1_audioclip 
    SET engagement_velocity = 
        (likes + (shares * 2)) / POWER(EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 + 2.0, 1.5)
    WHERE status = 'ready';
    """
    with connection.cursor() as cursor:
        cursor.execute(query)

@shared_task
def evolve_long_term_user_baselines():
    """
    Run every 24 hours at 3:00 AM.
    Prevents the user's long-term vector from stagnating indefinitely.
    """
    for user in User.objects.filter(is_active=True).iterator():
        new_sem, new_ac = calculate_time_decayed_vectors(user, limit=500)
        user.long_term_semantic = new_sem
        user.long_term_acoustic = new_ac
        user.save(update_fields=['long_term_semantic', 'long_term_acoustic'])


