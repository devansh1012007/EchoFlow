# Transcription & Tagging Pipeline

## Transcription (faster-whisper)

### Implementation (`tasks.py:251-255`)

```python
model = get_whisper_model()  # Lazy-loaded WhisperModel("base", cpu, int8)
segments, info = model.transcribe(normalized_path, beam_size=5)
transcript_text = " ".join([segment.text for segment in segments]).strip()
```

### Model Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Model | `base` | 74M params, good speed/accuracy tradeoff |
| Device | `cpu` | No GPU in default deployment |
| Compute type | `int8` | 4x smaller, 2x faster, minimal accuracy loss |
| Beam size | 5 | Better accuracy than greedy (1) |

### Whisper Model Sizes Comparison

| Model | Params | VRAM (fp32) | VRAM (int8) | Relative Speed | Quality |
|-------|--------|-------------|-------------|----------------|---------|
| tiny | 39M | ~1GB | ~256MB | 6x | Low |
| **base** | **74M** | **~1.5GB** | **~384MB** | **4x** | **Good** |
| small | 244M | ~5GB | ~1.2GB | 2.5x | Better |
| medium | 769M | ~15GB | ~3.8GB | 1.5x | High |
| large-v3 | 1550M | ~30GB | ~7.5GB | 1x | Best |

**Why base+int8:** Fits in 1GB RAM, processes 30s clip in ~5-10s on CPU.

### Transcription Output

```python
# segments = list of Segment objects
# Each segment: {start, end, text, avg_logprob, ...}
transcript_text = " ".join([segment.text for segment in segments]).strip()

# Example output:
# "hey everyone welcome back to another video today we're going to talk about"
```

### Language Detection
- `info.language` — detected language code
- `info.language_probability` — confidence
- Not currently used (could filter non-English)

### Error Handling
```python
try:
    model = get_whisper_model()
    segments, info = model.transcribe(normalized_path, beam_size=5)
except Exception as e:
    logger.exception("Local AI Processing Failed: %s", e)
    clip.status = 'failed'
    clip.save()
    return
```
- Any failure → clip.status = 'failed'
- No retry logic in task (Celery retry handles transient errors)

---

## Tag Extraction (KeyBERT)

### Implementation (`tasks.py:262-270`)

```python
keywords = get_kw_model().extract_keywords(
    transcript_text,
    keyphrase_ngram_range=(1, 1),  # Unigrams only
    stop_words='english',
    top_n=3,
)
clip.tags = [kw[0] for kw in keywords]  # e.g., ["comedy", "storytelling", "motivation"]
```

### KeyBERT Algorithm

1. **Extract candidates** — All words/phrases from document
2. **Embed candidates** — Same sentence-transformer model
3. **Embed document** — Transcript embedding
4. **Cosine similarity** — Candidate vs document
5. **Maximal Marginal Relevance (MMR)** — Diversity + relevance
6. **Return top-N** — Most representative keywords

### Parameters

| Parameter | Value | Effect |
|-----------|-------|--------|
| `keyphrase_ngram_range` | (1, 1) | Single words only (no phrases) |
| `stop_words` | 'english' | Removes common words |
| `top_n` | 3 | Exactly 3 tags per clip |

### Example Outputs

| Transcript | Tags |
|------------|------|
| "welcome back to my channel today we discuss python programming tips" | ["python", "programming", "tips"] |
| "this meditation will help you relax and find inner peace" | ["meditation", "relax", "peace"] |
| "breaking news the stock market crashed today investors panic" | ["stock", "market", "crashed"] |
| Instrumental (no speech) | ["instrumental"] (fallback) |

### Quality Considerations

**Strengths:**
- Unsupervised — no training data needed
- Fast — uses same embedding model
- Relevant — keywords actually appear in transcript

**Weaknesses:**
- **No semantic understanding** — "apple" (fruit) vs "apple" (company) same
- **No hierarchy** — "python programming" → ["python", "programming"] not "coding"
- **English stopwords only** — non-English clips get poor tags
- **Short clips** — 30s = few words, limited candidates

---

## Integration with Cold Start

### Tags → Vector Bootstrapping (`views.py:772-799`)

```python
@action(detail=False, methods=['post'], url_path='initialize')
def initialize_vectors(self, request):
    user = request.user
    selected_tags = request.data.get('selected_tags', [])
    
    # Find top 100 liked clips matching user's selected tags
    baseline_clips = AudioClip.objects.filter(
        tags__overlap=selected_tags,           # PostgreSQL JSONB overlap
        semantic_vector__isnull=False,
        acoustic_vector__isnull=False
    ).order_by('-likes')[:100]
    
    if not baseline_clips:
        return Response({"error": "Not enough data to build baseline."}, status=400)
        
    # Average vectors
    sem_vectors = [np.array(clip.semantic_vector) for clip in baseline_clips]
    ac_vectors = [np.array(clip.acoustic_vector) for clip in baseline_clips]
    
    user.long_term_semantic = np.mean(sem_vectors, axis=0).tolist()
    user.long_term_acoustic = np.mean(ac_vectors, axis=0).tolist()
    user.save()
    
    # Immediate feed refill
    refill_user_feed.delay(user.id, count=30)
```

### Tag Overlap Query
```python
tags__overlap=selected_tags  # PostgreSQL JSONB operator: any tag matches
```

### Cold Start Flow
```
New User Onboarding
    │
    ▼
Select 3-5 tags (e.g., ["comedy", "tech", "motivation"])
    │
    ▼
POST /tags/initialize/ {selected_tags}
    │
    ▼
Query top 100 liked clips with tag overlap
    │
    ▼
Average semantic + acoustic vectors
    │
    ▼
Store as user.long_term_semantic/acoustic
    │
    ▼
Trigger refill_user_feed (30 clips)
    │
    ▼
Feed ready with personalized recommendations
```

---

## Fallback Handling

### No Transcript (Instrumental)
```python
if transcript_text:
    # Normal path
else:
    clip.semantic_vector = [0.0] * 384
    clip.tags = ["instrumental"]
```

### Empty Tags
```python
keywords = get_kw_model().extract_keywords(...)
clip.tags = [kw[0] for kw in keywords] if keywords else ["general"]
```

### Transcription Failure
```python
try:
    # transcription + embedding + tagging
except Exception as e:
    clip.status = 'failed'
    clip.save()
    return
```
- Entire AI pipeline atomic — any failure = clip failed
- No partial success (e.g., acoustic vector saved but transcription failed)

---

## Performance

| Stage | Time (30s clip) | Memory |
|-------|-----------------|--------|
| Whisper transcribe | 5-15s | ~384MB (int8) |
| Embedding encode | 0.1s | ~100MB |
| KeyBERT extract | 0.5-1s | ~100MB |
| **Total AI** | **6-16s** | **~600MB peak** |

---

## Future Improvements

1. **Multi-language support** — detect language, use appropriate stopwords
2. **Phrase extraction** — ngram_range=(1, 2) for "machine learning" style tags
3. **Tag taxonomy** — map to predefined categories (music, comedy, education)
4. **Confidence scoring** — filter low-confidence tags
5. **LLM-based tagging** — use GPT for semantic tags (when API available)
6. **Speaker diarization** — separate speakers for interview-style content

---

*Source: `backend/app/tasks.py:251-279`, `backend/app/views.py:772-799`, `ai-ml/models/kw_wrapper.py`*