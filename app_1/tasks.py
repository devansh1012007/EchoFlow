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
    seen_key = f"user_seen:{user_id}"
    redis_client = cache.client.get_client()

    if redis_client.llen(redis_key) >= 20:
        return "Queue sufficient."

    sem_query, ac_query = calculate_blended_query_vectors(user)

    # 1. Fetch seen IDs directly from Redis SET (O(1) lookups, prevents giant SQL IN clauses)
    # If set doesn't exist, build it once.
    if not redis_client.exists(seen_key):
        seen_db = UserInteraction.objects.filter(user=user).values_list('clip_id', flat=True)
        if seen_db:
            redis_client.sadd(seen_key, *[str(cid) for cid in seen_db])
            redis_client.expire(seen_key, 604800) # 7 day TTL

    # Composite Ranking Base Query
    # Velocity = likes / (hours_since_upload + 1)
    velocity_expr = ExpressionWrapper(
        F('likes') / ((ExtractEpoch(Now()) - ExtractEpoch(F('created_at'))) / 3600.0 + 1.0),
        output_field=FloatField()
    )

    base_queryset = AudioClip.objects.filter(status='ready').annotate(
        engagement_velocity=velocity_expr
    )

    clip_ids_to_push = []
    creator_counts = {}

    def add_clip(clip):
        cid = str(clip.id)
        creator_id = clip.creator_id
        
        if redis_client.sismember(seen_key, cid):
            return False
            
        # Creator Diversity Enforcement: Max 2 per creator per batch
        if creator_counts.get(creator_id, 0) >= 2:
            return False

        clip_ids_to_push.append(cid)
        creator_counts[creator_id] = creator_counts.get(creator_id, 0) + 1
        redis_client.sadd(seen_key, cid)
        return True

    if sem_query and ac_query:
        exploit_count = int(count * 0.8)
        
        # Calculate composite score in DB
        exploit_clips = base_queryset.annotate(
            cosine_dist=CosineDistance('semantic_vector', sem_query) + CosineDistance('acoustic_vector', ac_query),
            # Formula: lower is better for sorting
            # Dist is 0-2. Velocity is high=good. We subtract velocity impact.
            composite_score=ExpressionWrapper(
                F('cosine_dist') - (F('engagement_velocity') * 0.05),
                output_field=FloatField()
            )
        ).order_by('composite_score')[:exploit_count * 3] # Fetch 3x to account for diversity drops

        for clip in exploit_clips:
            if len(clip_ids_to_push) >= exploit_count:
                break
            add_clip(clip)

        explore_count = count - len(clip_ids_to_push)
        explore_clips = base_queryset.order_by('-engagement_velocity')[:explore_count * 3]

        for clip in explore_clips:
            if len(clip_ids_to_push) >= count:
                break
            add_clip(clip)
    else:
        cold_clips = base_queryset.order_by('-engagement_velocity')[:count * 2]
        for clip in cold_clips:
            if len(clip_ids_to_push) >= count:
                break
            add_clip(clip)

    if not clip_ids_to_push:
        return "No new clips."

    import random
    random.shuffle(clip_ids_to_push)
    redis_client.rpush(redis_key, *clip_ids_to_push)

    return f"Added {len(clip_ids_to_push)} blended clips."

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