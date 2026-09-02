# Audio Normalization

## Purpose

**Single authoritative FFmpeg decode** before any processing:
- librosa, Whisper, HLS transcoding all use same normalized WAV
- Eliminates format-specific bugs
- Removes audioread fallback (deprecated in librosa 1.0)

---

## Implementation (`tasks.py:142-169`)

```python
def normalize_to_wav(input_file_path, sr=22050):
    """Decode arbitrary input audio (mp3/webm/ogg/m4a/whatever) 
    into clean mono PCM WAV via ffmpeg.
    
    Returns path to normalized WAV.
    Raises subprocess.CalledProcessError if ffmpeg can't decode.
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
```

---

## Parameters

| Parameter | Value | Reason |
|-----------|-------|--------|
| `-ac` | `1` | Mono (single channel) |
| `-ar` | `22050` | 22.05 kHz sample rate |
| `-f` | `wav` | PCM WAV output |
| `-y` | — | Overwrite without prompt |

---

## Why This Exists

### Problem: librosa's Decoder Chain
```python
# librosa.load() tries:
1. soundfile (libsndfile) — fast, native
2. audioread (fallback) — slow, uses FFmpeg/GStreamer
```
- **audioread deprecated** in librosa 1.0 (removed in 1.1)
- Different decoders → different outputs → inconsistent features
- Browser uploads: webm/opus/m4a often fail soundfile

### Solution: Authoritative Decode
```python
# ONE decode upfront
normalized_path = normalize_to_wav(uploaded_file)
# ALL downstream uses normalized_path:
y, sr = librosa.load(normalized_path, sr=22050)  # soundfile only
model.transcribe(normalized_path)                 # Whisper
ffmpeg -i normalized_path ...                     # HLS
```

---

## Usage in Pipeline (`tasks.py:210-224`)

```python
# 1. Download original from S3 to temp
fd, input_file_path = tempfile.mkstemp(suffix=ext)
with clip.original_file.open('rb') as remote:
    shutil.copyfileobj(remote, local_copy)

# 2. Normalize ONCE
try:
    normalized_path = normalize_to_wav(input_file_path)
except subprocess.CalledProcessError as e:
    clip.status = 'failed'
    clip.save()
    return

# 3. Cleanup original temp (no longer needed)
os.remove(input_file_path)

# 4. All downstream uses normalized_path
y, sr = librosa.load(normalized_path, sr=22050)
# Whisper
model.transcribe(normalized_path)
# HLS
ffmpeg -i normalized_path ...
```

---

## Scraper Normalization (`scrapers/normalizer.py`)

```python
def normalize_and_trim(in_path, out_path, max_seconds=300, target_format='mp3'):
    audio = AudioSegment.from_file(in_path)
    max_ms = int(max_seconds * 1000)
    if len(audio) > max_ms:
        audio = audio[:max_ms]
    audio = audio.set_frame_rate(44100).set_channels(2)
    audio.export(out_path, format=target_format, bitrate='192k')
    return out_path
```

**Differences from main pipeline:**
- Uses **pydub** (not direct FFmpeg)
- Output: **stereo 44.1kHz MP3** (not mono 22kHz WAV)
- Includes **trimming** to `max_seconds`
- Target format configurable (default MP3)

**Why different?**
- Scraper downloads already decoded by source
- pydub simpler for trim+normalize
- Output feeds into main pipeline's `normalize_to_wav()` anyway

---

## Sample Rate Choice: 22050 Hz

| Sample Rate | Use Case | Pros | Cons |
|-------------|----------|------|------|
| 44100 | CD quality | Full frequency range | 2x data |
| **22050** | **Speech/Music features** | **Half data, sufficient for MFCC** | **Loses >11kHz** |
| 16000 | Telephony | Minimal data | Loses high freq |

**Why 22050:**
- MFCC/chroma/mel features don't need >11kHz
- 2x faster processing, 2x less memory
- Whisper accepts any sample rate (resamples internally)
- HLS re-encodes to 44100 anyway

---

## Error Handling

```python
try:
    normalized_path = normalize_to_wav(input_file_path)
except subprocess.CalledProcessError as e:
    logger.error("Failed to normalize audio for clip %s: %s", clip_id, e.stderr.decode())
    clip.status = 'failed'
    clip.save()
    os.remove(input_file_path)
    return
```

**FFmpeg failures mean:** Input file is genuinely not valid audio (corrupt, wrong format, empty).

---

## Cleanup

```python
finally:
    # normalized_path cleaned up after all processing
    try:
        os.remove(normalized_path)
    except OSError:
        pass
    shutil.rmtree(local_hls_dir, ignore_errors=True)
```

**Guaranteed cleanup** even on:
- Exception in any stage
- Worker crash (OS reclaims temp files)
- OOM killer

---

## Verification

```bash
# Check normalized file
ffprobe normalized.wav
# Should show: mono, 22050Hz, pcm_s16le

# Compare original vs normalized duration
ffprobe -i original.mp3 -show_entries format=duration
ffprobe -i normalized.wav -show_entries format=duration
# Should match (within decode accuracy)
```

---

*Source: `backend/app/tasks.py:142-169, 210-224`, `backend/app/scrapers/normalizer.py`*