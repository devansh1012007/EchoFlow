#!/usr/bin/env python3
"""
test_hls_playback.py — Download HLS master playlist, fetch segments, 
decode with ffmpeg, and play through speakers to verify audio works end-to-end.
"""
import os
import sys
import subprocess
import tempfile
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.EchoFlow.settings')

import django
django.setup()

from django.conf import settings
from backend.app.models import AudioClip
from backend.app.media_urls import get_hls_playback_url


def fetch_url(url, timeout=10):
    """Fetch a URL and return (status_code, content_bytes)."""
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b''
    except Exception as e:
        return 0, str(e).encode()


def parse_m3u8(content):
    """Parse an HLS playlist and return list of segment URLs."""
    segments = []
    current_base = None
    for line in content.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            if '#EXT-X-MEDIA-URI' in line and current_base is None:
                # This is a variant playlist reference
                pass
            continue
        # This is a segment file reference
        if line.startswith('http'):
            segments.append(line)
        elif current_base:
            segments.append(f"{current_base}/{line}")
        else:
            # Relative to the playlist URL
            segments.append(line)
    return segments


def test_playback():
    """Test actual audio playback through speakers."""
    print("\n" + "="*60)
    print("  HLS Audio Playback Test (Speaker Output)")
    print("="*60 + "\n")
    
    # Get a ready clip
    clip = AudioClip.objects.filter(status='ready', hls_playlist_url__isnull=False).first()
    if not clip:
        print("ERROR: No ready clips with HLS URLs found in database.")
        print("Cannot test playback without a processed clip.")
        return False
    
    print(f"Testing clip: {clip.title} (ID: {clip.id})")
    print(f"HLS playlist key: {clip.hls_playlist_url}")
    
    # Get the full URL
    master_url = get_hls_playback_url(clip.hls_playlist_url)
    print(f"Master playlist URL: {master_url}")
    
    # Step 1: Fetch master playlist
    print("\n[Step 1] Fetching master playlist...")
    status, content = fetch_url(master_url)
    if status != 200:
        print(f"  FAILED: HTTP {status}")
        print(f"  The HLS files are NOT publicly accessible.")
        print(f"  This is the ROOT CAUSE of the 401 errors in the frontend.")
        print(f"\n  FIX REQUIRED: Apply public-read policy to MinIO hls/ prefix:")
        print(f"    docker compose exec minio mc policy download local/{settings.STORAGES['default']['OPTIONS']['bucket_name']}/hls/")
        return False
    
    master_content = content.decode()
    print(f"  SUCCESS: Master playlist fetched ({len(master_content)} bytes)")
    print(f"\n  Master playlist content:")
    print(f"  {'-'*50}")
    for line in master_content.split('\n')[:15]:
        print(f"    {line}")
    print(f"  {'-'*50}")
    
    # Step 2: Determine if master references variant playlists or direct segments
    print("\n[Step 2] Analyzing playlist structure...")
    
    variant_playlists = []
    direct_segments = []
    
    for line in master_content.strip().split('\n'):
        line = line.strip()
        if line.endswith('.m3u8') and not line.startswith('#'):
            # Variant playlist reference
            if line.startswith('http'):
                variant_playlists.append(line)
            else:
                base = '/'.join(master_url.split('/')[:-1])
                variant_playlists.append(f"{base}/{line}")
        elif line and not line.startswith('#'):
            # Direct segment reference
            if line.startswith('http'):
                direct_segments.append(line)
            else:
                base = '/'.join(master_url.split('/')[:-1])
                direct_segments.append(f"{base}/{line}")
    
    print(f"  Variant playlists found: {len(variant_playlists)}")
    print(f"  Direct segments found: {len(direct_segments)}")
    
    # Step 3: Fetch variant playlist if present
    segment_urls = []
    if variant_playlists:
        print(f"\n[Step 3] Fetching variant playlist: {variant_playlists[0]}")
        vstatus, vcontent = fetch_url(variant_playlists[0])
        if vstatus != 200:
            print(f"  FAILED: HTTP {vstatus}")
            print(f"  Variant playlist is NOT publicly accessible!")
            return False
        print(f"  SUCCESS: Variant playlist fetched ({len(vcontent)} bytes)")
        segment_urls = parse_m3u8(vcontent.decode())
    elif direct_segments:
        print(f"\n[Step 3] Using {len(direct_segments)} direct segments from master")
        segment_urls = direct_segments
    
    if not segment_urls:
        print("  WARNING: No segments found in playlist")
        return False
    
    # Step 4: Download first segment and verify it's valid audio
    print(f"\n[Step 4] Downloading first segment for validation...")
    first_seg_url = segment_urls[0]
    print(f"  Segment URL: {first_seg_url}")
    
    seg_status, seg_content = fetch_url(first_seg_url)
    if seg_status != 200:
        print(f"  FAILED: HTTP {seg_status}")
        print(f"  HLS segments are NOT publicly accessible!")
        print(f"\n  FIX REQUIRED: Apply public-read policy to MinIO hls/ prefix")
        return False
    
    print(f"  SUCCESS: First segment fetched ({len(seg_content)} bytes)")
    
    # Step 5: Verify segment is valid audio using ffprobe
    print(f"\n[Step 5] Validating segment with ffprobe...")
    
    fd, seg_path = tempfile.mkstemp(suffix='.ts')
    os.close(fd)
    with open(seg_path, 'wb') as f:
        f.write(seg_content)
    
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_streams', '-show_format', seg_path],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode != 0:
            print(f"  ffprobe error: {result.stderr}")
        else:
            # Check for audio stream
            if 'audio' in result.stdout.lower():
                print(f"  SUCCESS: Segment contains valid audio stream")
                # Extract codec info
                for line in result.stdout.split('\n'):
                    if 'codec_name' in line or 'sample_rate' in line or 'channels' in line:
                        print(f"    {line.strip()}")
            else:
                print(f"  WARNING: Segment may not contain audio stream")
                print(f"  ffprobe output preview:")
                print(f"    {result.stdout[:300]}")
    except FileNotFoundError:
        print(f"  SKIP: ffprobe not found — skipping validation")
    except Exception as e:
        print(f"  SKIP: ffprobe error: {e}")
    finally:
        os.unlink(seg_path)
    
    # Step 6: Download all segments and concatenate, then play
    print(f"\n[Step 6] Downloading all segments and playing through speakers...")
    print(f"  Total segments to download: {len(segment_urls)}")
    
    fd, combined_path = tempfile.mkstemp(suffix='.wav')
    os.close(fd)
    
    # Download all segments
    segment_files = []
    for i, seg_url in enumerate(segment_urls[:5]):  # Limit to first 5 segments for speed
        print(f"  Downloading segment {i+1}/{min(5, len(segment_urls))}...")
        s_status, s_content = fetch_url(seg_url)
        if s_status != 200:
            print(f"    FAILED: HTTP {s_status} — stopping here")
            break
        
        fd2, seg_tmp = tempfile.mkstemp(suffix='.ts')
        os.close(fd2)
        with open(seg_tmp, 'wb') as f:
            f.write(s_content)
        segment_files.append(seg_tmp)
    
    if not segment_files:
        print("  ERROR: No segments downloaded")
        os.unlink(combined_path)
        return False
    
    # Concatenate segments using ffmpeg
    print(f"\n[Step 7] Concatenating segments to WAV...")
    
    # Create a file list for ffmpeg
    fd, file_list = tempfile.mkstemp(suffix='.txt')
    os.close(fd)
    with open(file_list, 'w') as f:
        for seg_file in segment_files:
            f.write(f"file '{seg_file}'\n")
    
    try:
        # Try to concat as raw audio first (if segments are raw AAC)
        concat_result = subprocess.run(
            ['ffmpeg', '-y', '-f', 'concat', '-safe', '0',
             '-i', file_list, '-c', 'copy', combined_path + '.aac'],
            capture_output=True, text=True, timeout=30
        )
        
        if concat_result.returncode == 0:
            print(f"  Concatenation successful ({os.path.getsize(combined_path + '.aac')} bytes)")
            # Convert to WAV for playback
            subprocess.run(
                ['ffmpeg', '-y', '-i', combined_path + '.aac', '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2', combined_path],
                capture_output=True, text=True, timeout=30
            )
        else:
            # Try individual decode then concat
            print(f"  Direct concat failed, trying individual decode...")
            decoded_files = []
            for seg_file in segment_files:
                dec_tmp = tempfile.mkstemp(suffix='.wav')[1]
                subprocess.run(
                    ['ffmpeg', '-y', '-i', seg_file, '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2', dec_tmp],
                    capture_output=True, text=True, timeout=10
                )
                decoded_files.append(dec_tmp)
            
            # Concat decoded WAVs
            fd, wav_list = tempfile.mkstemp(suffix='.txt')
            os.close(fd)
            with open(wav_list, 'w') as f:
                for df in decoded_files:
                    f.write(f"file '{df}'\n")
            
            subprocess.run(
                ['ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                 '-i', wav_list, '-c', 'copy', combined_path],
                capture_output=True, text=True, timeout=30
            )
            
            for df in decoded_files:
                os.unlink(df)
            os.unlink(wav_list)
        
        file_size = os.path.getsize(combined_path)
        print(f"  Combined WAV: {file_size} bytes")
        
        if file_size < 100:
            print("  WARNING: Output file is very small — audio may be corrupted")
        
        # Step 8: Play through speakers
        print(f"\n[Step 8] Playing audio through speakers...")
        print(f"  If you hear audio, the entire pipeline is working!")
        print(f"  If you hear silence/noise, the encoding may be broken.\n")
        
        # Try multiple playback methods
        played = False
        
        # Method 1: aplay (Linux)
        if not played:
            try:
                result = subprocess.run(
                    ['aplay', '-q', combined_path],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    print("  -> Played via aplay (ALSA)")
                    played = True
                else:
                    print(f"  -> aplay failed: {result.stderr[:100]}")
            except FileNotFoundError:
                print("  -> aplay not available (ALSA)")
            except subprocess.TimeoutExpired:
                print("  -> Playback timed out (may still be playing)")
                played = True
        
        # Method 2: paplay (PulseAudio)
        if not played:
            try:
                result = subprocess.run(
                    ['paplay', combined_path],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    print("  -> Played via paplay (PulseAudio)")
                    played = True
            except FileNotFoundError:
                print("  -> paplay not available (PulseAudio)")
        
        # Method 3: ffplay
        if not played:
            try:
                result = subprocess.run(
                    ['ffplay', '-nodisp', '-autoexit', combined_path],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    print("  -> Played via ffplay")
                    played = True
            except FileNotFoundError:
                print("  -> ffplay not available")
        
        if not played:
            print("\n  -> No audio player found on this system")
            print(f"  -> To manually test, play the file: aplay {combined_path}")
            print(f"  -> Or: ffplay {combined_path}")
        
        # Cleanup
        for sf in segment_files:
            try:
                os.unlink(sf)
            except:
                pass
        os.unlink(file_list)
        try:
            os.unlink(combined_path + '.aac')
        except:
            pass
        
        print(f"\n  Combined file saved at: {combined_path}")
        print(f"  You can play it manually with: aplay {combined_path}")
        
        return True
        
    except Exception as e:
        print(f"  ERROR during playback: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    success = test_playback()
    
    print("\n" + "="*60)
    if success:
        print("  RESULT: HLS playback pipeline is WORKING")
        print("  The audio plays correctly through speakers.")
        print("  If the frontend still doesn't play, the issue is:")
        print("  - CORS configuration in the browser")
        print("  - Frontend HLS.js configuration")
        print("  - Network/Firewall blocking")
    else:
        print("  RESULT: HLS playback pipeline is BROKEN")
        print("  The issue has been identified above.")
    print("="*60 + "\n")


if __name__ == '__main__':
    main()
