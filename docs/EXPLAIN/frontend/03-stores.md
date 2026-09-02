# Frontend State Management (Context Stores)

## Overview

Four React Context providers manage global state:

```
main.tsx provider composition:
<ThemeProvider>
  <ToastProvider>
    <AuthProvider>
      <PlayerProvider>
        <AppRouter />
      </PlayerProvider>
    </AuthProvider>
  </ToastProvider>
</ThemeProvider>
```

---

## 1. Auth Store (`stores/auth.tsx`)

### Context Value
```typescript
interface AuthContextValue {
  user: User | null;
  authed: boolean;
  loading: boolean;
  login: (username, password) => Promise<void>;
  register: (email, username, password) => Promise<void>;
  logout: () => void;
  patchUser: (Partial<User>) => void;
}
```

### State
```typescript
const [user, setUser] = useState<User | null>(loadUser);      // from sessionStorage
const [authed, setAuthed] = useState<boolean>(!!loadToken()); // from sessionStorage
const [loading, setLoading] = useState(false);
```

### Key Functions

**login(username, password)**
```typescript
const d = await authAPI.login(username, password);
persist(d, d.user);  // setTokens + sessionStorage.ef_user + setUser + setAuthed
```

**register(email, username, password)**
```typescript
const d = await authAPI.register(email, username, password);
persist(d, d.user);
sessionStorage.setItem('ef_new_user', '1');  // triggers onboarding
```

**logout()**
```typescript
clearTokens();  // sessionStorage.removeItem(ef_access, ef_refresh, ef_user)
setUser(null);
setAuthed(false);
```

**Session expiry listener:**
```typescript
useEffect(() => {
  const handle = () => logout();
  window.addEventListener('ef_session_expired', handle);
  return () => window.removeEventListener('ef_session_expired', handle);
}, [logout]);
```

### Persistence
- Tokens in `sessionStorage` (cleared on tab close)
- User object serialized to `sessionStorage.ef_user`
- `ef_new_user` flag triggers onboarding modal once

---

## 2. Player Store (`stores/player.tsx`)

### Context Value
```typescript
interface PlayerState {
  active: AudioClip | null;      // Currently loaded clip
  playing: boolean;              // Is playing
  progress: number;              // 0-1 playback progress
  duration: number;              // Total duration (seconds)
  buffered: number;              // 0-1 buffered amount
  isBuffering: boolean;          // Waiting for buffer
  error: string | null;          // Playback error
  
  play: (clip: AudioClip) => void;
  pause: () => void;
  seek: (fraction: number) => void;
  skipForward: (seconds?: number) => void;
  skipBackward: (seconds?: number) => void;
  listenMs: () => number;        // Ms since play started
  destroy: () => void;           // Cleanup
  loadHLSIfNeeded: (src: string) => void;
}
```

### Core Implementation

**Audio & HLS Refs:**
```typescript
const audioRef = useRef<HTMLAudioElement>(new Audio());
const hlsRef = useRef<Hls | null>(null);
const startRef = useRef<number | null>(null);  // For watch_time_ms calculation
```

**Event Handlers (attached once):**
```typescript
useEffect(() => {
  const a = audioRef.current;
  a.ontimeupdate = () => {
    if (a.duration) {
      setProgress(a.currentTime / a.duration);
      setDuration(a.duration);
    }
    if (a.buffered.length) setBuffered(a.buffered.end(a.buffered.length - 1) / (a.duration || 1));
  };
  a.onwaiting = () => setBuffering(true);
  a.onplaying = () => setBuffering(false);
  a.onended = () => { setPlaying(false); setProgress(1); };
  a.onerror = () => { setError('Playback error'); setPlaying(false); };
  return () => { killHLS(); };
}, [killHLS]);
```

**HLS Loading:**
```typescript
const loadSource = useCallback((clip: AudioClip) => {
  const a = audioRef.current;
  killHLS();
  a.pause();
  setError(null);
  setProgress(0); setDuration(0); setBuffered(0);
  startRef.current = Date.now();

  const src = clip.hls_playlist_url;
  if (!src) { setError('No stream available'); return; }

  const fullSrc = src.startsWith('http') ? src : (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8005') + src;

  if (Hls.isSupported()) {
    const hls = new Hls({ startLevel: -1, maxBufferLength: 30 });
    hls.loadSource(fullSrc);
    hls.attachMedia(a);
    hls.on(Hls.Events.MANIFEST_PARSED, () => { a.play().catch(() => {}); setPlaying(true); });
    hls.on(Hls.Events.ERROR, (_e, d) => { if (d.fatal && d.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad(); });
    hlsRef.current = hls;
  } else if (a.canPlayType('application/vnd.apple.mpegurl')) {
    a.src = fullSrc; a.play().catch(() => {}); setPlaying(true);  // Safari native
  } else {
    a.src = fullSrc; a.play().catch(() => {}); setPlaying(true);  // Fallback
  }
}, [killHLS]);
```

**Play/Seek Controls:**
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

const seek = useCallback((r: number) => { 
  if (audioRef.current && duration) { 
    audioRef.current.currentTime = r * duration; 
    setProgress(r); 
  } 
}, [duration]);
```

**Telemetry on Unmount:**
```typescript
useEffect(() => {
  if (!active) return;
  return () => {
    const ms = listenMs();
    if (ms > 800) interactionsAPI.logTelemetry(active.id, { action_type: 'view', watch_time_ms: ms }).catch(() => {});
  };
}, [active, listenMs]);
```

### HLS.js Configuration
```typescript
new Hls({
  startLevel: -1,        // Auto bitrate
  maxBufferLength: 30,   // Seconds to buffer ahead
})
```

---

## 3. Theme Store (`stores/theme.tsx`)

```typescript
type ThemeMode = 'dark' | 'light' | 'system';

interface ThemeContextValue {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  resolved: 'dark' | 'light';  // Actual applied theme
}
```

**Implementation:**
- Persists to `localStorage` (survives tab close)
- Resolves `system` via `window.matchMedia('(prefers-color-scheme: dark)')`
- Applies `data-theme` attribute to `<html>` for CSS variables
- **Currently dark-only** — light mode not implemented in CSS

---

## 4. Toast Store (`stores/toast.tsx`)

```typescript
interface ToastContextValue {
  toast: (msg: string, type?: 'success'|'error'|'info'|'warn', ms?: number) => void;
}
```

**Implementation:**
- Single toast at a time (replaces previous)
- Auto-dismiss after 4s (configurable)
- Renders fixed-position element via `ToastProvider`
- Types map to CSS variables: `--terracotta` (error), `--sage` (success), etc.

---

## Usage Patterns

### Consuming Context
```typescript
import { useAuth } from '../stores/auth';
import { usePlayer } from '../stores/player';
import { useToast } from '../stores/toast';

function MyComponent() {
  const { user, authed, login, logout } = useAuth();
  const { active, playing, play, pause, seek } = usePlayer();
  const toast = useToast();
  
  // ...
}
```

### Provider Composition (main.tsx)
```typescript
root.render(
  <React.StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <AuthProvider>
          <PlayerProvider>
            <AppRouter />
          </PlayerProvider>
        </AuthProvider>
      </ToastProvider>
    </ThemeProvider>
  </React.StrictMode>
);
```

**Order matters:** Auth must be inside Theme/Toast for access; Player inside Auth for user-specific playback.

---

## State Persistence Summary

| Store | Persistence | Key | Survives Tab Close |
|-------|-------------|-----|-------------------|
| Auth | sessionStorage | ef_access, ef_refresh, ef_user | ❌ |
| Player | Memory only | — | ❌ |
| Theme | localStorage | ef_theme | ✅ |
| Toast | Memory only | — | ❌ |

---

## Known Limitations

1. **No persistence for playback position** — refresh loses position
2. **Single toast** — rapid toasts replace each other
3. **Theme dark-only** — light/system modes not styled
4. **No queue** — Player plays one clip at a time (no playlist)
5. **No background audio** — pauses on tab hide (browser policy)
6. **Context re-renders** — all consumers re-render on any state change

---

*Source: `frontend/sample_frontend/src/stores/*.tsx`*