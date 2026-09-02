# Frontend HLS Playback

## Overview

Audio playback powered by **HLS.js** with native Safari fallback. Persistent `MiniPlayer` + full-screen `ReelCard` integration.

---

## Architecture

```
PlayerProvider (Context)
├── audioRef: HTMLAudioElement
├── hlsRef: Hls instance
├── startRef: Date.now() for watch_time_ms
│
├── loadSource(clip) → HLS.js or native
├── play(clip) → load or resume
├── pause/seek/skip controls
└── cleanup on unmount → logTelemetry
    │
    ▼
MiniPlayer (AppShell) — persistent bottom bar
ReelCard (Feed) — full-screen visual + tap to play
```

---

## HLS.js Integration

### Initialization (`stores/player.tsx:loadSource`)

```typescript
const fullSrc = src.startsWith('http') ? src : (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8005') + src;

if (Hls.isSupported()) {
  const hls = new Hls({ 
    startLevel: -1,        // Auto quality
    maxBufferLength: 30,   // Buffer 30s ahead
  });
  hls.loadSource(fullSrc);
  hls.attachMedia(audioRef.current);
  
  hls.on(Hls.Events.MANIFEST_PARSED, () => {
    audioRef.current.play().catch(() => {});
    setPlaying(true);
  });
  
  hls.on(Hls.Events.ERROR, (_e, data) => {
    if (data.fatal && data.type === Hls.ErrorTypes.NETWORK_ERROR) {
      hls.startLoad();  // Auto-recover network errors
    }
  });
  
  hlsRef.current = hls;
} else if (audioRef.current.canPlayType('application/vnd.apple.mpegurl')) {
  // Safari native HLS
  audioRef.current.src = fullSrc;
  audioRef.current.play().catch(() => {});
  setPlaying(true);
} else {
  // Fallback (unlikely)
  audioRef.current.src = fullSrc;
  audioRef.current.play().catch(() => {});
  setPlaying(true);
}
```

### Key HLS.js Options
| Option | Value | Purpose |
|--------|-------|---------|
| `startLevel` | -1 | Auto bitrate selection |
| `maxBufferLength` | 30 | Seconds to buffer ahead |
| `maxBufferSize` | (default) | Max buffer bytes |
| `maxBufferHole` | (default) | Max hole in buffer |

---

## Native Safari HLS Support

```typescript
} else if (audioRef.current.canPlayType('application/vnd.apple.mpegurl')) {
  audioRef.current.src = fullSrc;
  audioRef.current.play().catch(() => {});
  setPlaying(true);
}
```
- Safari supports HLS natively via `<audio>` element
- No HLS.js needed → better battery life
- `canPlayType` check prevents HLS.js load on Safari

---

## Playback Controls

### Play/Pause Toggle
```typescript
const play = useCallback(async (clip: AudioClip) => {
  if (active?.id === clip.id) {
    if (playing) { audioRef.current.pause(); setPlaying(false); }
    else { await audioRef.current.play().catch(() => {}); setPlaying(true); startRef.current = Date.now(); }
    return;
  }
  loadSource(clip);
  setActive(clip);
}, [active, playing, loadSource]);
```

### Seek
```typescript
const seek = useCallback((r: number) => {
  if (audioRef.current && duration) {
    audioRef.current.currentTime = r * duration;
    setProgress(r);
  }
}, [duration]);
```

### Skip Forward/Back (10s default)
```typescript
const skipForward = useCallback((s: number = 10) => {
  if (audioRef.current && duration) {
    audioRef.current.currentTime = Math.min(audioRef.current.currentTime + s, duration);
  }
}, [duration]);

const skipBackward = useCallback((s: number = 10) => {
  if (audioRef.current) {
    audioRef.current.currentTime = Math.max(audioRef.current.currentTime - s, 0);
  }
}, []);
```

---

## Auto-Play & Auto-Advance

### IntersectionObserver Auto-Play (`components/feed/ReelList.tsx`)

```typescript
useEffect(() => {
  if (!clips.length) return;
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const idx = Number((entry.target as HTMLElement).dataset.index);
        const clip = clips[idx];
        if (activeIdRef.current !== clip.id) {
          play(clip);
        }
      }
    });
  }, { threshold: 0.6 });  // 60% visible
  
  itemRefs.current.forEach(ref => ref && obs.observe(ref));
  return () => obs.disconnect();
}, [clips, play]);
```

### Auto-Advance on Completion
```typescript
useEffect(() => {
  if (active && progress >= 0.99) {
    const idx = clips.findIndex(c => c.id === active.id);
    if (idx >= 0 && idx < clips.length - 1) {
      const timer = setTimeout(() => {
        const next = itemRefs.current[idx + 1];
        next?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 1000);
      return () => clearTimeout(timer);
    }
  }
}, [active, progress, clips]);
```

---

## Visual Feedback

### Play/Pause Overlay (`ReelCard.tsx`)
```typescript
{showPlayIcon && (
  <div className="absolute inset-0 flex items-center justify-center z-10">
    <div className="w-24 h-24 rounded-full bg-black/40 backdrop-blur-md flex items-center justify-center animate-popIn">
      {isPlaying ? (
        <div className="flex items-end gap-1 h-6">
          {[12, 18, 14, 20, 14, 18, 12].map((h, i) => (
            <div key={i} className="wave-bar w-1 rounded" style={{height: h, background: '#fff'}} />
          ))}
        </div>
      ) : (
        <svg className="w-7 h-8" viewBox="0 0 24 24" fill="#fff">
          <polygon points="6,4 22,12 6,20" />
        </svg>
      )}
    </div>
  </div>
)}
```

### Waveform Visualization (`components/audio/WaveformBar.tsx`)
- Animated bars synced to playback progress
- Static visualization (not real-time FFT)
- Uses `clip.duration_ms` for scaling

---

## Telemetry & Analytics

### Watch Time Tracking
```typescript
const listenMs = useCallback(() => startRef.current ? Date.now() - startRef.current : 0, []);

// On unmount or clip change:
useEffect(() => {
  if (!active) return;
  return () => {
    const ms = listenMs();
    if (ms > 800) {  // Ignore <800ms (accidental taps)
      interactionsAPI.logTelemetry(active.id, { 
        action_type: 'view', 
        watch_time_ms: ms 
      }).catch(() => {});
    }
  };
}, [active, listenMs]);
```

**Server-side:** `ClipInteractionViewSet.log_telemetry()` calculates `completion_rate = watch_time_ms / clip.duration_ms`

---

## Error Handling

### Network Error Recovery
```typescript
hls.on(Hls.Events.ERROR, (_e, data) => {
  if (data.fatal && data.type === Hls.ErrorTypes.NETWORK_ERROR) {
    hls.startLoad();  // Retry loading
  }
  // Other fatal errors: MANIFEST_PARSE_ERROR, MEDIA_ERROR
  // Not auto-recovered — shows error overlay
});
```

### Error State
```typescript
const [error, setError] = useState<string | null>(null);
// Set on audio.onerror, HLS fatal errors
// Cleared on loadSource()
```

---

## MiniPlayer Component (`components/feed/MiniPlayer.tsx`)

**Persistent bottom bar** showing:
- Current clip title/creator
- Play/pause button
- Progress bar with seek
- Skip forward/back (10s)
- Expands to full ReelCard on tap

**Stays mounted** across route changes (in `AppShell`).

---

## Performance Considerations

| Aspect | Implementation |
|--------|----------------|
| Buffer management | `maxBufferLength: 30` prevents excessive memory |
| Cleanup | `killHLS()` destroys HLS instance on clip change |
| Memory | Single `Audio` element reused |
| IntersectionObserver | Efficient visibility detection (no scroll listeners) |
| Re-renders | `useCallback` for all handlers, `React.memo` on `ReelCard` |

---

## Browser Compatibility

| Browser | HLS Support | Implementation |
|---------|-------------|----------------|
| Chrome/Edge | HLS.js | MSE + HLS.js |
| Firefox | HLS.js | MSE + HLS.js |
| Safari | Native | `<audio src=".m3u8">` |
| iOS Safari | Native | `<audio src=".m3u8">` |
| Android Chrome | HLS.js | MSE + HLS.js |

---

## Known Issues

1. **No background playback** — pauses when tab hidden (requires Media Session API)
2. **No playlist/queue** — single clip at a time
3. **Seek on live streams** — not applicable (VOD only)
4. **Quality selection UI** — not exposed (auto only)
4. **AirPlay/Chromecast** — not supported
5. **Audio focus** — no ducking for notifications

---

*Source: `frontend/sample_frontend/src/stores/player.tsx`, `frontend/sample_frontend/src/components/feed/ReelList.tsx`, `frontend/sample_frontend/src/components/feed/MiniPlayer.tsx`, `frontend/sample_frontend/src/components/audio/ReelCard.tsx`*