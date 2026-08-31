#!/usr/bin/env python3
"""
check_pipeline.py — Full pipeline diagnostic: DB → API → MinIO → HLS playback
Tests every step from database to browser playback.
"""
import os
import sys
import subprocess
import json
import urllib.request
import urllib.error

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.EchoFlow.settings')

import django
django.setup()

from django.conf import settings
from backend.app.models import AudioClip
from backend.app.media_urls import get_hls_playback_url


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def test_1_database():
    """Check database for ready clips and their HLS URLs."""
    section("TEST 1: Database — Ready Clips")
    
    ready_clips = AudioClip.objects.filter(status='ready')
    processing_clips = AudioClip.objects.filter(status='processing')
    failed_clips = AudioClip.objects.filter(status='failed')
    
    print(f"  Ready clips:        {ready_clips.count()}")
    print(f"  Processing clips:   {processing_clips.count()}")
    print(f"  Failed clips:       {failed_clips.count()}")
    print(f"  Total clips:        {AudioClip.objects.count()}")
    
    if ready_clips.exists():
        print(f"\n  Sample ready clips:")
        for clip in ready_clips[:5]:
            print(f"    - ID: {clip.id}")
            print(f"      Title: {clip.title}")
            print(f"      Status: {clip.status}")
            print(f"      HLS URL field: {clip.hls_playlist_url}")
            if clip.hls_playlist_url:
                full_url = get_hls_playback_url(clip.hls_playlist_url)
                print(f"      Full URL: {full_url}")
            print()
    else:
        print("  WARNING: No ready clips in database!")
        print("  HLS playback requires clips with status='ready'")


def test_2_media_urls():
    """Test URL construction."""
    section("TEST 2: Media URL Construction")
    
    print(f"  PUBLIC_MEDIA_ENDPOINT_URL: {settings.PUBLIC_MEDIA_ENDPOINT_URL}")
    print(f"  AWS_S3_ENDPOINT_URL: {settings.STORAGES['default']['OPTIONS'].get('endpoint_url')}")
    print(f"  Bucket name: {settings.STORAGES['default']['OPTIONS'].get('bucket_name')}")
    print(f"  Addressing style: {settings.STORAGES['default']['OPTIONS'].get('addressing_style')}")
    
    # Test URL construction
    test_key = "hls/test-clip-123/master.m3u8"
    constructed = get_hls_playback_url(test_key)
    print(f"\n  Test key: {test_key}")
    print(f"  Constructed URL: {constructed}")
    
    expected = f"{settings.PUBLIC_MEDIA_ENDPOINT_URL.rstrip('/')}/{settings.STORAGES['default']['OPTIONS']['bucket_name']}/{test_key}"
    if constructed == expected:
        print("  -> URL construction: OK")
    else:
        print(f"  -> URL construction: MISMATCH")
        print(f"     Expected: {expected}")


def test_3_minio_api():
    """Test MinIO API from host."""
    section("TEST 3: MinIO API Accessibility")
    
    endpoint = settings.PUBLIC_MEDIA_ENDPOINT_URL or "http://localhost:9000"
    bucket = settings.STORAGES['default']['OPTIONS']['bucket_name']
    access_key = settings.STORAGES['default']['OPTIONS']['access_key']
    secret_key = settings.STORAGES['default']['OPTIONS']['secret_key']
    
    # Test basic connectivity
    url = f"{endpoint.rstrip('/')}/"
    try:
        req = urllib.request.Request(url)
        req.add_header('Authorization', f'Bearer test')
        resp = urllib.request.urlopen(req, timeout=5)
        print(f"  -> MinIO reachable: YES (HTTP {resp.status})")
    except urllib.error.HTTPError as e:
        print(f"  -> MinIO reachable: YES (HTTP {e.code})")
    except Exception as e:
        print(f"  -> MinIO reachable: NO — {e}")
        return
    
    # Test bucket listing
    url = f"{endpoint.rstrip('/')}/{bucket}?list-type=2"
    try:
        from minio import Minio
        from minio.error import S3Error
    except ImportError:
        print("  -> Skipping MinIO SDK test (minio package not installed)")
        return
    
    client = Minio(
        endpoint.split('//')[1],
        access_key=access_key,
        secret_key=secret_key,
        secure=False,
    )
    
    try:
        objects = list(client.list_objects(bucket, recursive=True, prefix='hls/'))
        print(f"  -> HLS objects in bucket: {len(objects)}")
        for obj in objects[:10]:
            print(f"     - {obj.object_name} ({obj.size} bytes)")
    except Exception as e:
        print(f"  -> Error listing objects: {e}")


def test_4_hls_url_accessibility():
    """Test if HLS URLs are accessible from host (no auth required)."""
    section("TEST 4: HLS URL Accessibility (No Auth)")
    
    endpoint = settings.PUBLIC_MEDIA_ENDPOINT_URL or "http://localhost:9000"
    bucket = settings.STORAGES['default']['OPTIONS']['bucket_name']
    
    ready_clips = AudioClip.objects.filter(status='ready', hls_playlist_url__isnull=False)[:3]
    
    if not ready_clips.exists():
        print("  -> No ready clips with HLS URLs to test")
        return
    
    for clip in ready_clips:
        hls_url = get_hls_playback_url(clip.hls_playlist_url)
        print(f"\n  Testing clip {clip.id}:")
        print(f"  HLS URL: {hls_url}")
        
        # Test master playlist
        try:
            resp = urllib.request.urlopen(hls_url, timeout=5)
            content = resp.read().decode()
            print(f"  -> master.m3u8: HTTP {resp.status} ({len(content)} bytes)")
            print(f"     Preview: {content[:200]}")
            
            # Check if it references variant playlists or segments
            if 'index.m3u8' in content or 'index1.ts' in content or '#EXT' in content:
                print("  -> Master playlist format: VALID HLS")
            else:
                print("  -> WARNING: Master playlist doesn't look like valid HLS")
                
        except urllib.error.HTTPError as e:
            print(f"  -> master.m3u8: HTTP {e.code} — {'FORBIDDEN' if e.code == 403 else 'UNAUTHORIZED' if e.code == 401 else 'ERROR'}")
            print(f"     THIS IS THE BUG — HLS files need public-read policy!")
        except Exception as e:
            print(f"  -> master.m3u8: Connection error — {e}")


def test_5_celery_media_env():
    """Check celery_media worker environment."""
    section("TEST 5: Celery Media Worker Environment")
    
    # Check if celery_media has the right env vars
    result = subprocess.run(
        ['docker', 'compose', 'exec', 'celery_media', 'env'],
        capture_output=True, text=True, timeout=10
    )
    
    env_vars = {}
    for line in result.stdout.split('\n'):
        if '=' in line:
            k, _, v = line.partition('=')
            env_vars[k] = v
    
    critical_vars = [
        'PUBLIC_MEDIA_ENDPOINT_URL',
        'AWS_S3_ENDPOINT_URL', 
        'AWS_ACCESS_KEY_ID',
        'AWS_SECRET_ACCESS_KEY',
        'AWS_STORAGE_BUCKET_NAME',
    ]
    
    print("  Critical environment variables in celery_media:")
    for var in critical_vars:
        val = env_vars.get(var, '<NOT SET>')
        status = "OK" if val != '<NOT SET>' else "MISSING"
        print(f"    {var}: {val} [{status}]")


def test_6_hls_segment_format():
    """Test HLS segment format and compatibility."""
    section("TEST 6: HLS Segment Format Check")
    
    endpoint = settings.PUBLIC_MEDIA_ENDPOINT_URL or "http://localhost:9000"
    bucket = settings.STORAGES['default']['OPTIONS']['bucket_name']
    
    ready_clips = AudioClip.objects.filter(status='ready', hls_playlist_url__isnull=False)[:1]
    
    for clip in ready_clips:
        hls_url = get_hls_playback_url(clip.hls_playlist_url)
        
        try:
            resp = urllib.request.urlopen(hls_url, timeout=5)
            content = resp.read().decode()
            
            print(f"  Master playlist content:")
            print(f"  {'-'*50}")
            for i, line in enumerate(content.split('\n')[:20]):
                print(f"    {i+1}: {line}")
            print(f"  {'-'*50}")
            
            # Check for common issues
            if '#EXTM3U' not in content:
                print("  -> ERROR: Missing #EXTM3U header")
            if '#EXT-X-VERSION' not in content:
                print("  -> WARNING: Missing #EXT-X-VERSION")
            if 'index.m3u8' in content:
                print("  -> Contains variant playlist reference: index.m3u8")
                # Try to fetch the variant playlist
                variant_url = hls_url.replace('master.m3u8', 'index.m3u8')
                try:
                    vresp = urllib.request.urlopen(variant_url, timeout=5)
                    vcontent = vresp.read().decode()
                    print(f"  -> Variant playlist ({len(vcontent)} bytes):")
                    for line in vcontent.split('\n')[:15]:
                        print(f"       {line}")
                    
                    # Check segment format
                    if '.ts' in vcontent:
                        print("  -> Segment format: MPEG-TS (.ts)")
                    elif 'index1' in vcontent or 'index0' in vcontent:
                        print("  -> Segment format: fMP4 (potential compatibility issue)")
                        print("  -> WARNING: fMP4 segments may not work with all hls.js configurations")
                        
                except urllib.error.HTTPError as e:
                    print(f"  -> Variant playlist: HTTP {e.code} (not public!)")
                    
        except urllib.error.HTTPError as e:
            print(f"  -> Cannot fetch master playlist: HTTP {e.code}")
        except Exception as e:
            print(f"  -> Error: {e}")


def test_7_ffmpeg_codec_check():
    """Check what codec FFmpeg produces."""
    section("TEST 7: FFmpeg Codec Check")
    
    # Check if ffmpeg is available and what AAC codecs it supports
    result = subprocess.run(
        ['ffmpeg', '-encoders'],
        capture_output=True, text=True, timeout=5
    )
    
    if 'aac' in result.stdout.lower():
        print("  -> FFmpeg AAC encoder: AVAILABLE")
        # Find the AAC encoder line
        for line in result.stdout.split('\n'):
            if 'aac' in line.lower() and 'enc' not in line.lower():
                print(f"     {line.strip()}")
    else:
        print("  -> WARNING: FFmpeg AAC encoder NOT found!")
        print("  -> HLS encoding will FAIL without AAC support")
    
    # Check fMP4 support
    if 'fmp4' in result.stdout.lower() or 'iso mp4' in result.stdout.lower():
        print("  -> FFmpeg fMP4 support: AVAILABLE")
    else:
        print("  -> FFmpeg fMP4 support: NOT AVAILABLE")
        print("  -> HLS with -hls_segment_type fmp4 will FAIL")


def main():
    print("\n" + "="*60)
    print("  EchoFlow Full Pipeline Diagnostic")
    print("="*60)
    
    try:
        test_1_database()
        test_2_media_urls()
        test_3_minio_api()
        test_4_hls_url_accessibility()
        test_5_celery_media_env()
        test_6_hls_segment_format()
        test_7_ffmpeg_codec_check()
    except Exception as e:
        print(f"\n  ERROR during diagnostics: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"\n{'='*60}")
    print("  Diagnostics Complete")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
