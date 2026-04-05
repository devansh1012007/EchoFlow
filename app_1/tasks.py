import os
import subprocess
from celery import shared_task
from django.conf import settings
from .models import AudioClip

@shared_task
def process_audio_to_hls(clip_id):
    """
    Takes a raw uploaded file and converts it into a multi-bitrate HLS stream.
    """
    clip = AudioClip.objects.get(id=clip_id)
    input_file_path = clip.original_file.path
    
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

@shared_task
def refill_user_feed(user_id, count=50):
    """
    Background task to calculate the next batch of clips for a user
    and push them into their Redis Hot Queue.
    """
    redis_key = f"user_feed:{user_id}"
    redis_client = cache.client.get_client()

    # 1. Check current queue size so we don't overfill it
    current_size = redis_client.llen(redis_key)
    if current_size >= 20:
        return f"Queue sufficient for user {user_id}. Size: {current_size}"

    # 2. Get the IDs of clips the user has ALREADY interacted with
    # (We don't want to show them clips they've already liked or skipped)
    seen_clip_ids = UserInteraction.objects.filter(
        user_id=user_id
    ).values_list('clip_id', flat=True)

    # 3. THE ALGORITHM (Retrieval)
    # For now, we fetch the newest clips they haven't seen.
    # Later, you will replace this with pgvector similarity searches.
    new_clips = AudioClip.objects.filter(
        status='ready'
    ).exclude(
        id__in=seen_clip_ids
    ).order_by('-created_at')[:count]

    # If there are no new clips, exit safely
    if not new_clips:
        return f"No new clips available for user {user_id}"

    # 4. Push the new Clip IDs into the Redis List
    # rpush adds them to the right (the back of the line)
    clip_ids_to_push = [str(clip.id) for clip in new_clips]
    
    # We unpack the list using * to push all IDs in one fast Redis command
    redis_client.rpush(redis_key, *clip_ids_to_push)

    return f"Added {len(clip_ids_to_push)} clips to user {user_id}'s queue."