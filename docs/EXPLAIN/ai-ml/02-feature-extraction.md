# Feature Extraction: Acoustic & Semantic Vectors

## Acoustic Vector Extraction (librosa)

**Function:** `backend/app/tasks.py:extract_acoustic_vector(y, sr)`

### Algorithm

```python
def extract_acoustic_vector(y, sr):
    # y: audio time series (mono, 22050Hz)
    # sr: sample rate (22050)
    
    # 1. MFCC — 40 dimensions
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=40).mean(axis=1)
    
    # 2. Chroma — 12 dimensions  
    chroma = librosa.feature.chroma_stft(y=y, sr=sr).mean(axis=1)
    
    # 3. Mel Spectrogram — 76 dimensions
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=76).mean(axis=1)
    
    # Concatenate: 40 + 12 + 76 = 128
    acoustic_vector = np.concatenate((mfcc, chroma, mel))
    
    # Normalize for cosine similarity
    norm = np.linalg.norm(acoustic_vector)
    if norm > 0:
        acoustic_vector = acoustic_vector / norm
        
    return acoustic_vector.tolist()
```

### Component Details

#### MFCC (40 dims) — Timbre & Voice Texture
- **Mel-Frequency Cepstral Coefficients**
- Captures spectral envelope (formants)
- Good for: speaker identification, instrument recognition, genre classification
- `n_mfcc=40` — standard for music/speech (13-40 typical)

#### Chroma (12 dims) — Harmonic/Pitch Content
- **Chroma Feature** (Pitch Class Profile)
- 12 bins = 12 semitones (C, C#, D, ..., B)
- Captures: key, chord progression, harmonic similarity
- Good for: cover detection, key estimation, Western music similarity

#### Mel Spectrogram (76 dims) — Spectral Energy Distribution
- **Mel-scaled Spectrogram** (perceptual frequency scale)
- `n_mels=76` — energy in 76 mel bands
- Captures: brightness, spectral centroid, overall timbre
- Good for: broad genre classification, mood detection

### Normalization
```python
norm = np.linalg.norm(acoustic_vector)
if norm > 0:
    acoustic_vector = acoustic_vector / norm
```
- Ensures unit length for cosine similarity
- Cosine similarity = dot product of normalized vectors
- Range: [-1, 1] where 1 = identical direction

### Usage in Recommendation

```python
# In refill_user_feed (tasks.py:539-541)
composite_query = base_queryset.annotate(
    sem_dist=CosineDistance('semantic_vector', sem_query),
    ac_dist=CosineDistance('acoustic_vector', ac_query),
    vector_similarity=ExpressionWrapper(
        1.0 - ((F('sem_dist') + F('ac_dist')) / 4.0),
        output_field=FloatField()
    ),
    # ...
)
```

**Combined distance:** `(semantic_cosine_dist + acoustic_cosine_dist) / 4`
- Each cosine distance ∈ [0, 2] (for normalized vectors)
- Sum ∈ [0, 4]
- `1 - sum/4` → similarity ∈ [0, 1]

---

## Semantic Vector Extraction (sentence-transformers)

**Function:** `backend/app/tasks.py:process_audio_to_hls` (lines 258-261)

### Algorithm

```python
# After Whisper transcription:
if transcript_text:
    embed_model = get_embedding_model()  # all-MiniLM-L6-v2
    vector = embed_model.encode(transcript_text)  # numpy array (384,)
    clip.semantic_vector = vector.tolist()
else:
    # Instrumental fallback
    clip.semantic_vector = [0.0] * 384
    clip.tags = ["instrumental"]
```

### Model: all-MiniLM-L6-v2

| Property | Value |
|----------|-------|
| Dimensions | 384 |
| Layers | 6 (distilled from MiniLM-L12) |
| Max seq length | 256 tokens |
| Training | Contrastive learning on 1B+ sentence pairs |
| Speed | ~1000 sentences/sec on CPU |
| Quality | Strong for semantic similarity |

### Why This Model?
- **Small & fast** — 22M parameters, CPU-friendly
- **Good quality** — Near BERT-base on STS benchmarks
- **Multilingual** — Works on 50+ languages
- **No fine-tuning needed** — General purpose embeddings

### Fallback for Instrumental
```python
clip.semantic_vector = [0.0] * 384
clip.tags = ["instrumental"]
```
- Zero vector = no semantic preference
- Will match poorly on semantic similarity (relies on acoustic)
- Tag "instrumental" enables category filtering

---

## Vector Storage

### Database Schema
```python
# AudioClip model
semantic_vector = VectorField(dimensions=384, null=True, blank=True)
acoustic_vector = VectorField(dimensions=128, null=True, blank=True)

# User model (long-term baselines)
long_term_semantic = VectorField(dimensions=384, null=True, blank=True)
long_term_acoustic = VectorField(dimensions=128, null=True, blank=True)
```

### HNSW Indexes (PostgreSQL + pgvector)
```python
# AudioClip.Meta.indexes
HnswIndex(
    name='semantic_vector_index',
    fields=['semantic_vector'],
    m=16,
    ef_construction=64,
    opclasses=['vector_cosine_ops']
),
HnswIndex(
    name='acoustic_vector_index',
    fields=['acoustic_vector'],
    m=16,
    ef_construction=64,
    opclasses=['vector_cosine_ops']
)
```

**HNSW Parameters:**
| Param | Value | Effect |
|-------|-------|--------|
| `m` | 16 | Connections per node (memory vs recall) |
| `ef_construction` | 64 | Build-time search width (quality vs speed) |
| `opclasses` | `vector_cosine_ops` | Cosine distance operator class |

**Index size estimate:** ~1.5GB per 10M vectors (384-dim)

---

## Querying Vectors

### CosineDistance (pgvector.django)
```python
from pgvector.django import CosineDistance

AudioClip.objects.annotate(
    sem_dist=CosineDistance('semantic_vector', query_vector),
    ac_dist=CosineDistance('acoustic_vector', query_vector)
).order_by('sem_dist')  # Ascending = most similar first
```

**Returns:** Distance ∈ [0, 2] for normalized vectors
- 0 = identical
- 1 = orthogonal  
- 2 = opposite

### Combined Similarity Score
```python
vector_similarity = 1.0 - ((sem_dist + ac_dist) / 4.0)
# sem_dist ∈ [0,2], ac_dist ∈ [0,2]
# sum ∈ [0,4], divided by 4 → [0,1]
# 1 - that → [0,1] where 1 = most similar
```

---

## User Preference Vectors

### Long-term Baseline (User model)
- Updated hourly by `evolve_long_term_user_baselines` task
- Blended from recent interactions (limit=500)
- Stored on User: `long_term_semantic`, `long_term_acoustic`

### Short-term Context (calculate_time_decayed_vectors)
```python
def calculate_time_decayed_vectors(user, limit=50):
    recent_interactions = UserInteraction.objects.filter(user=user).select_related('clip').order_by('-created_at')[:limit]
    
    for interaction in recent_interactions:
        # Time decay: 1 / (1 + log(hours_since))
        hours_ago = (now - interaction.created_at).total_seconds() / 3600.0
        time_weight = 1.0 / (1.0 + math.log1p(max(0, hours_ago)))
        
        # Completion weight: interaction.completion_rate (0-1)
        comp_weight = interaction.completion_rate if interaction.completion_rate > 0 else 0.1
        
        # Intent weight: like/share=1.5, skip<20%=-0.5, else 1.0
        if interaction.interaction_type in ['like', 'share']:
            intent_weight = 1.5
        elif interaction.interaction_type == 'skip' and interaction.completion_rate < 0.2:
            intent_weight = -0.5
        else:
            intent_weight = 1.0
            
        final_weight = time_weight * comp_weight * intent_weight
        # Accumulate weighted vectors...
    
    # Blend: 70% context + 30% long-term baseline
    ALPHA = 0.7
    final_sem = (ALPHA * weighted_sem) + ((1 - ALPHA) * long_term_semantic)
    # Normalize result
```

---

## Quality Considerations

### Acoustic Vector Quality
- **Sample rate:** 22050Hz (downsampled from 44100) — sufficient for features
- **Mono only** — stereo collapsed, loses spatial info
- **Mean pooling** — loses temporal dynamics (could use statistics: mean, std, skew)
- **Fixed dimensions** — 128 may be limiting for complex audio

### Semantic Vector Quality
- **Transcript dependent** — ASR errors propagate to embedding
- **Short clips** — 30s clips = ~50-100 words, limited context
- **Model limit** — 256 token max (not binding for short clips)
- **Language bias** — English-optimized, multilingual but weaker

### Combined Similarity
- **Equal weighting** of semantic + acoustic in distance sum
- Could weight by clip type (music vs speech)
- No learned fusion — simple average

---

## Future Improvements

1. **Temporal acoustic features** — statistics over time windows
2. **Prosody features** — pitch, energy, rhythm (via librosa)
3. **Speaker embeddings** — for voice similarity
4. **Fine-tuned semantic model** — on audio transcript domain
5. **Learned fusion** — MLP to combine semantic + acoustic
6. **Dimensionality reduction** — PCA/UMAP for visualization

---

*Source: `backend/app/tasks.py:100-139, 251-270`, `backend/app/models.py`, `backend/app/migrations/0001_initial.py`*