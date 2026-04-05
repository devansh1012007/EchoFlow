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

@shared_task
def refill_user_feed(user_id, count=50):
    redis_key = f"user_feed:{user_id}"
    redis_client = cache.client.get_client()

    if redis_client.llen(redis_key) >= 20:
        return "Queue sufficient."

    # 1. Get the clips they have already seen
    seen_clip_ids = list(UserInteraction.objects.filter(
        user_id=user_id
    ).values_list('clip_id', flat=True))
    
    # Fetch the clips currently sitting in their Redis queue so we don't duplicate them
    queued_bytes = redis_client.lrange(redis_key, 0, -1)
    queued_ids = [vid.decode('utf-8') for vid in queued_bytes]
    seen_clip_ids.extend(queued_ids)

    # 2. Get their current mood vector
    target_vector = calculate_dynamic_user_vector(user_id)

    if target_vector:
        # ALGORITHM A: Vector Similarity Search
        # Order by closest mathematical distance to their current mood
        new_clips = AudioClip.objects.filter(
            status='ready'
        ).exclude(
            id__in=seen_clip_ids
        ).order_by(
            CosineDistance('vibe_vector', target_vector)
        )[:count]
    else:
        # ALGORITHM B: The Cold Start (New Users)
        # If they have no history, serve highly-shared global content
        new_clips = AudioClip.objects.filter(
            status='ready'
        ).exclude(
            id__in=seen_clip_ids
        ).order_by('-shares', '-created_at')[:count]

    if not new_clips:
        return "No new clips."

    # Push to Redis
    clip_ids_to_push = [str(clip.id) for clip in new_clips]
    redis_client.rpush(redis_key, *clip_ids_to_push)

    return f"Added {len(clip_ids_to_push)} vector-matched clips."




from pgvector.django import CosineDistance
from django.db.models import F

def calculate_blended_query_vectors(user):
    """
    Blends the user's short-term mood (Context) with their historical baseline (Long-Term).
    """
    # 1. Calculate Context Vector (Short-Term Mood)
    recent_interactions = UserInteraction.objects.filter(
        user=user, interaction_type__in=['like', 'share']
    ).select_related('clip').order_by('-created_at')[:10]
    
    if not recent_interactions:
        return user.long_term_semantic, user.long_term_acoustic

    sem_vectors = [np.array(i.clip.semantic_vector) for i in recent_interactions if i.clip.semantic_vector]
    ac_vectors = [np.array(i.clip.acoustic_vector) for i in recent_interactions if i.clip.acoustic_vector]
    
    context_sem = np.mean(sem_vectors, axis=0) if sem_vectors else None
    context_ac = np.mean(ac_vectors, axis=0) if ac_vectors else None

    # 2. Blend Context (70%) with Long-Term (30%)
    ALPHA = 0.7
    
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
    seen_ids = list(UserInteraction.objects.filter(user=user).values_list('clip_id', flat=True))
    
    sem_query, ac_query = calculate_blended_query_vectors(user)

    # The Addictive Query: Combining Semantic (Meaning) and Acoustic (Vibe) distances
    queryset = AudioClip.objects.filter(status='ready').exclude(id__in=seen_ids)
    
    if sem_query and ac_query:
        # We calculate the combined distance in PostgreSQL natively
        queryset = queryset.annotate(
            combined_distance=(
                CosineDistance('semantic_vector', sem_query) + 
                CosineDistance('acoustic_vector', ac_query)
            )
        ).order_by('combined_distance')
    
    new_clips = queryset[:count]
    
    # ... [Push IDs to Redis Queue] ...