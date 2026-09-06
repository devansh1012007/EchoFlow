# EchoFlow Frontend Fix Plan — Comprehensive Implementation Document

**Status**: Ready for Implementation  
**Author**: Lead Agent  
**Date**: 2026-09-06  
**Target**: `/frontend/sample_frontend` (TypeScript/Vite/React Router)

---

## 1. Executive Summary

This document describes the complete plan to fix the broken logic in `/frontend/sample_frontend` and align it with the canonical behavior from `/frontend/main.jsx` (the single-file original prototype). The sample_frontend is a TypeScript/Vite/React Router reimplementation that currently has multiple critical bugs preventing proper SPA behavior, incorrect data flows, and missing features.

### 1.1 Scope

| Area | Current State | Target State |
|------|---------------|--------------|
| Navigation | Full-page reload via `window.location.href` | True SPA using `react-router`'s `useNavigate` |
| Auth Flow | Double-check, race conditions | Single boundary, proper session handling |
| HLS Playback | Token required, no fallback | Token-first with graceful fallback to direct HLS |
| Player Architecture | CDN-loaded hls.js + mixed logic | NPM-bundled hls.js, clean separation |
| Visuals | Gradient backgrounds only | Cover image support with WaveformBar fallback |
| Demo Mode | Module-level flag | Reactive React Context |
| Audio Upload | No cover image support | Cover image upload + display |

### 1.2 Key Decisions (Already Agreed)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Theme/Colors | Terracotta/Sage (DESIGN.md) | Sample_frontend already uses this |
| Shell Architecture | Shared `AppShell` with BottomNav/MiniPlayer | Better maintainability than page-specific |
| HLS.js Loading | NPM import + bundled | Tree-shakable, no CDN race conditions |
| Waveform Visualization | Optional — image first, WaveformBar fallback | Richer UX, backward compatible |
| HLS Token | Keep in sample_frontend, add fallback | Matches backend security model |

---

## 2. Current Architecture Analysis

### 2.1 File Structure Overview

```
/frontend/sample_frontend/
├── src/
│   ├── main.tsx                      # App entry, provider composition
│   ├── app/
│   │   ├── router.tsx                # React Router config, auth guards
│   │   └── AppShell.tsx              # Shared shell (BottomNav, MiniPlayer)
│   ├── api/
│   │   └── client.ts                 # API layer, token management
│   ├── components/
│   │   ├── audio/
│   │   │   ├── ReelCard.tsx          # Core reel component
│   │   │   └── WaveformBar.tsx       # Playback progress bar
│   │   ├── comments/CommentSheet.tsx
│   │   ├── common/{atoms,molecules,NetworkBanner}.tsx
│   │   ├── feed/{MiniPlayer,ReelList,OnboardingModal}.tsx
│   │   ├── navigation/BottomNav.tsx
│   │   └── sharing/ShareModal.tsx
│   ├── data/
│   │   ├── feedAdapter.ts            # Demo/live mode, data fetching
│   │   ├── clips.ts                  # Category colors, clip factories
│   │   ├── demo*.ts                  # Demo data
│   │   └── creators.ts
│   ├── hooks/
│   │   └── useBackendStatus.ts       # Backend health check
│   ├── pages/                        # Page components (Feed, Profile, etc.)
│   ├── stores/
│   │   ├── auth.tsx                  # Auth context + session
│   │   ├── player.tsx                # Audio player + HLS
│   │   ├── toast.tsx                 # Toast notifications
│   │   └── theme.tsx                 # Dark/light theme
│   ├── types/index.ts                # TypeScript interfaces
│   └── styles/globals.css            # DESIGN.md palette + Tailwind
```

### 2.2 Data Flow: Current vs Target

#### Current (Broken) Navigation Flow
```
User clicks ReelCard avatar
    → ProfilePage calls `go('profile', { userId: id })` (prop)
    → `go` = `navTo` in router.tsx (module-level function)
    → `navTo` does `window.location.href = '/profile?userId=123'`
    → FULL PAGE RELOAD
    → All React state lost (player, toasts, auth context re-initializes)
    → RootRedirect in router.tsx reads sessionStorage → /feed
    → User lands on feed, not profile
```

#### Target (Fixed) Navigation Flow
```
User clicks ReelCard avatar
    → ProfilePage calls `navigate('/profile', { state: { userId: id } })`
    → React Router updates URL, renders ProfilePage
    → NO RELOAD — player continues, toasts persist, auth state intact
    → ProfilePage reads `userId` from `useParams()` or location.state
```

#### Current HLS Playback Flow (player.tsx)
```
play(clip) called
    → loadSource(clip)
    → await mediaAPI.getPlaybackToken(clip.id)  // REQUIRED
    → If 403 (unmoderated) or 404: THROW ERROR, playback blocked
    → If success: load HLS via hls.js or native
```

#### Target HLS Playback Flow
```
play(clip) called
    → loadSource(clip)
    → try: await mediaAPI.getPlaybackToken(clip.id)
    → If 403/404: log warning, FALLBACK to direct HLS load
    → If success: load HLS with token cookie already set
    → If native HLS (Safari): direct load works
```

### 2.3 Key Interfaces

**AudioClip (sample_frontend/src/types/index.ts:20-41)**
```typescript
interface AudioClip {
  id: string;
  title: string;
  category: string;
  creator: User;
  creator_name: string;
  creator_id?: number;
  hls_playlist_url: string | null;
  duration_ms: number;
  likes: number;
  shares: number;
  skips: number;
  comment_count: number;
  is_liked: boolean;
  tags: string[];
  status: 'processing' | 'ready' | 'failed';
  created_at?: string;
  description?: string;
  source_name?: string | null;
  source_url?: string | null;
  license?: string | null;
  // NEW: cover_image?: string | null;  // To be added
}
```

**PlayerState (sample_frontend/src/types/index.ts:93-109)**
```typescript
interface PlayerState {
  active: AudioClip | null;
  playing: boolean;
  progress: number;
  duration: number;
  buffered: number;
  isBuffering: boolean;
  error: string | null;
  play: (clip: AudioClip) => void;
  pause: () => void;
  seek: (fraction: number) => void;
  skipForward: (seconds?: number) => void;
  skipBackward: (seconds?: number) => void;
  listenMs: () => number;
  destroy: () => void;
  loadHLSIfNeeded: (src: string) => void;
}
```

---

## 3. Detailed Issue Inventory

### 3.1 Critical Issues (Block Core Functionality)

| # | Issue | File:Line | Root Cause | Severity |
|---|-------|-----------|------------|----------|
| C1 | Full-page reload on every navigation | `router.tsx:52-55` | `navTo` uses `window.location.href` | Critical |
| C2 | Profile `go` ignores params | `router.tsx:66-70` | Local `go` function drops second arg | Critical |
| C3 | Stale closure in ProfilePage.setNewName | `Profile.tsx:51` | Uses `prof` state instead of `d` from async | Critical |
| C4 | Double auth boundary check | `router.tsx:17-21,72-74` + `AppShell.tsx:13` | RequireAuth + Protected + AppShell all check | Major |
| C5 | Register race: `ef_new_user` set after auth | `auth.tsx:59` vs `AppShell.tsx:30-35` | AppShell effect runs on `authed` before key set | Major |

### 3.2 Major Issues (Incorrect Behavior)

| # | Issue | File:Line | Root Cause | Severity |
|---|-------|-----------|------------|----------|
| M1 | HLS token blocks playback for unmoderated clips | `player.tsx:60-66` | No fallback on 403/404 | Major |
| M2 | BackendWatcher doesn't re-enable live mode | `router.tsx:76-81`, `feedAdapter.ts:6-12` | Module-level `demoMode` never resets | Major |
| M3 | `shareAPI.getUnread` response shape mismatch | `AppShell.tsx:22-23` | Assumes `{ unread }` but API may return `{ count }` | Major |
| M4 | ReelList layout: 100vh container + 100vh items | `ReelList.tsx:71-72,83-84` | Double height, scroll broken | Major |
| M5 | WaveformBar rendered but no cover image support | `ReelCard.tsx:234` | No image field in AudioClip type | Major |

### 3.3 Minor Issues (Code Quality)

| # | Issue | File:Line | Fix |
|---|-------|-----------|-----|
| m1 | ESLint disable for exhaustive-deps | `Feed.tsx:63` | Fix deps or memoize |
| m2 | TypeScript `JSX.Element` deprecated | `router.tsx:17,72` | Use `React.ReactNode` |
| m3 | CDN hls.js fallback logic | `player.tsx:349-363` | Remove — use NPM import |
| m4 | `useToast` optional in PlayerProvider | `player.tsx:9` | Make required or add default |

---

## 4. Implementation Phases

### Phase 1: Navigation Architecture (Foundation)

#### 1.1 Create NavigationContext
**File**: `src/context/NavigationContext.tsx` (NEW)

```typescript
import { createContext, useContext, ReactNode } from 'react';
import { useNavigate, NavigateOptions } from 'react-router-dom';

interface NavigationContextValue {
  go: (path: string, params?: Record<string, unknown>, options?: NavigateOptions) => void;
}

const NavigationContext = createContext<NavigationContextValue | null>(null);

export function NavigationProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  
  const go = (path: string, params?: Record<string, unknown>, options?: NavigateOptions) => {
    let finalPath = path;
    if (params && Object.keys(params).length > 0) {
      const searchParams = new URLSearchParams();
      Object.entries(params).forEach(([key, value]) => {
        searchParams.set(key, String(value));
      });
      finalPath = `${path}?${searchParams.toString()}`;
    }
    navigate(finalPath, options);
  };
  
  return (
    <NavigationContext.Provider value={{ go }}>
      {children}
    </NavigationContext.Provider>
  );
}

export function useNavigation() {
  const ctx = useContext(NavigationContext);
  if (!ctx) throw new Error('useNavigation must be used within NavigationProvider');
  return ctx;
}
```

**Changes**:
- Wraps `useNavigate` from react-router
- Builds query string from params object
- Provides `go(path, params?)` API compatible with existing page calls

#### 1.2 Refactor router.tsx
**File**: `src/app/router.tsx`

**Removals**:
- `navTo` function (lines 52-55)
- `ProfilePageWrapper` local `go` (lines 66-70)
- `LoginRouter` local `go` (lines 62-64)

**Additions**:
- Import `NavigationProvider`, `useNavigation`
- Wrap `<Routes>` with `<NavigationProvider>`
- Remove `go` prop drilling — pages use `useNavigation()`

**Route Structure** (unchanged but cleaner):
```tsx
<NavigationProvider>
  <BrowserRouter>
    <Routes>
      <Route path="/login" element={<LoginRouter />} />
      <Route path="/dev/demo" element={<Protected><AppShell page="devdemo"><DeveloperDemoPage /></AppShell></Protected>} />
      <Route path="/" element={<RootRedirect />} />
      <Route path="/feed" element={<Protected><AppShell page="feed"><FeedPage /></AppShell></Protected>} />
      <Route path="/explore" element={<Protected><AppShell page="explore"><ExplorePage /></AppShell></Protected>} />
      <Route path="/profile" element={<Protected><AppShell page="profile"><ProfilePage /></AppShell></Protected>} />
      <Route path="/profile/:userId" element={<Protected><AppShell page="profile"><ProfilePage /></AppShell></Protected>} />
      <Route path="/inbox" element={<Protected><AppShell page="inbox"><InboxPage /></AppShell></Protected>} />
      <Route path="/library" element={<Protected><AppShell page="library"><LibraryPage /></AppShell></Protected>} />
      <Route path="/upload" element={<Protected><AppShell page="upload"><UploadPage /></AppShell></Protected>} />
      <Route path="/settings" element={<Protected><AppShell page="settings"><SettingsPage /></AppShell></Protected>} />
      <Route path="/public/clips/:id" element={<PublicClipRedirect />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    <BackendWatcher />
  </BrowserRouter>
</NavigationProvider>
```

#### 1.3 Update All Pages to Use `useNavigation()`
**Files**: `Feed.tsx`, `Explore.tsx`, `Profile.tsx`, `Inbox.tsx`, `Library.tsx`, `Upload.tsx`, `Settings.tsx`, `Login.tsx`, `DeveloperDemo.tsx`

**Pattern**:
```tsx
// Before
export function FeedPage({ go }: Props) { ... }

// After
export function FeedPage() {
  const { go } = useNavigation();
  // go('profile', { userId: id }) works correctly
}
```

### Phase 2: Critical Bug Fixes

#### 2.1 Fix ProfilePage Stale Closure
**File**: `src/pages/Profile.tsx:51`

```typescript
// Before (broken)
setNewName(prof?.username || '');

// After (fixed)
setNewName(d.username || '');
```
Uses `d` (the fresh API response) instead of stale `prof` state.

#### 2.2 Add HLS Token Fallback
**File**: `src/stores/player.tsx:44-82` (`loadSource` function)

```typescript
const loadSource = useCallback(async (clip: AudioClip) => {
  const a = audioRef.current;
  killHLS();
  a.pause();
  setError(null);
  setProgress(0); setDuration(0); setBuffered(0);
  startRef.current = Date.now();

  const src = clip.hls_playlist_url;
  if (!src) { setError('No stream available'); return; }

  const fullSrc = src.startsWith('http') ? src : (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8005') + src;

  // Try to get playback token first
  let hasToken = false;
  try {
    await mediaAPI.getPlaybackToken(clip.id);
    hasToken = true;
  } catch (tokenError) {
    // Token issuance failed (403 unmoderated, 404 not found, network error)
    // Log but continue — we'll try direct HLS load
    console.warn('Playback token unavailable, attempting direct HLS load:', tokenError);
  }

  if (Hls.isSupported()) {
    const hls = new Hls({ startLevel: -1, maxBufferLength: 30 });
    hls.loadSource(fullSrc);
    hls.attachMedia(a);
    hls.on(Hls.Events.MANIFEST_PARSED, () => { a.play().catch(() => {}); setPlaying(true); });
    hls.on(Hls.Events.ERROR, (_e, d) => { if (d.fatal && d.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad(); });
    hlsRef.current = hls;
  } else if (a.canPlayType('application/vnd.apple.mpegurl')) {
    a.src = fullSrc; a.play().catch(() => {}); setPlaying(true);
  } else {
    a.src = fullSrc; a.play().catch(() => {}); setPlaying(true);
  }
}, [killHLS]);
```

**Key Changes**:
- Token request is now best-effort
- On failure (403/404/network), log warning and continue
- Direct HLS load attempted regardless of token status
- Matches main.jsx's resilient behavior

#### 2.3 Fix Demo Mode Reactivity
**File**: `src/data/feedAdapter.ts` + NEW `src/context/DemoModeContext.tsx`

**New Context** (`src/context/DemoModeContext.tsx`):
```typescript
import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { useBackendStatus } from '../hooks/useBackendStatus';

interface DemoModeContextValue {
  isDemoMode: boolean;
}

const DemoModeContext = createContext<DemoModeContextValue | null>(null);

export function DemoModeProvider({ children }: { children: ReactNode }) {
  const [isDemo, setIsDemo] = useState(true);
  const backendStatus = useBackendStatus();

  useEffect(() => {
    if (backendStatus !== null) {
      setIsDemo(!backendStatus);
    }
  }, [backendStatus]);

  return (
    <DemoModeContext.Provider value={{ isDemoMode: isDemo }}>
      {children}
    </DemoModeContext.Provider>
  );
}

export function useDemoMode() {
  const ctx = useContext(DemoModeContext);
  if (!ctx) throw new Error('useDemoMode must be used within DemoModeProvider');
  return ctx.isDemoMode;
}
```

**Update feedAdapter.ts**:
```typescript
// Remove module-level demoMode variable
// Replace isDemoMode() function with hook usage in components
// Keep fetchFeed/fetchSuggestions logic but check demo via context
```

**Update components** to use `useDemoMode()` hook instead of `isDemoMode()` function.

#### 2.4 Fix ReelList Layout
**File**: `src/components/feed/ReelList.tsx:71-88`

```tsx
// Before (broken)
<div style={{
  height: '100vh',
  overflowY: 'auto', scrollSnapType: 'y mandatory',
  // ...
}}>
  {clips.map((clip, i) => (
    <div key={clip.id} style={{
      flex: '0 0 100vh',  // <-- PROBLEM: 100vh per item
      scrollSnapAlign: 'center',
      // ...
    }}>
```

```tsx
// After (fixed)
<div style={{
  height: 'calc(100vh - 80px)',  // Account for BottomNav (70px) + MiniPlayer (16px top)
  overflowY: 'auto', scrollSnapType: 'y mandatory',
  // ...
}}>
  {clips.map((clip, i) => (
    <div key={clip.id} style={{
      flex: '0 0 calc(100vh - 80px)',  // Match container height
      scrollSnapAlign: 'center',
      minHeight: 'calc(100vh - 80px)', // Ensure minimum height
      // ...
    }}>
```

### Phase 3: Cover Image Support

#### 3.1 Backend: Add Thumbnail Field to AudioClip Model
**File**: `backend/app/models.py` (AudioClip model)

```python
# Add to AudioClip class
cover_image = models.ImageField(
    upload_to='covers/%Y/%m/%d/', 
    blank=True, 
    null=True,
    help_text='Optional cover image for the audio reel'
)
```

**Migration**: Run `makemigrations` + `migrate`

#### 3.2 Backend: Add Cover Image to FeedClipSerializer
**File**: `backend/app/serializers.py` (FeedClipSerializer)

```python
class FeedClipSerializer(serializers.ModelSerializer):
    # ... existing fields ...
    cover_image = serializers.SerializerMethodField()

    class Meta:
        fields = [
            'id', 'title', 'creator_name', 'category',
            'hls_playlist_url', 'likes', 'shares', 'skips', 
            'comment_count', 'is_liked', 'creator_id',
            'cover_image',  # NEW
        ]

    def get_cover_image(self, obj):
        if obj.cover_image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.cover_image.url)
        return None
```

#### 3.3 Frontend: Update AudioClip Type
**File**: `src/types/index.ts:20-41`

```typescript
export interface AudioClip {
  // ... existing fields ...
  cover_image?: string | null;  // NEW
}
```

#### 3.4 Frontend: Update UploadPage for Cover Image
**File**: `src/pages/Upload.tsx`

Add cover image upload field:
```tsx
const [coverFile, setCoverFile] = useState<File | null>(null);
// ... in submit():
if (coverFile) fd.append('cover_image', coverFile);
```

Add UI for cover image picker (similar to audio file drop zone but for images).

#### 3.5 Frontend: Update ReelCard to Show Cover Image
**File**: `src/components/audio/ReelCard.tsx:94-172`

Replace the gradient background with conditional rendering:

```tsx
{/* Visual header — show cover image if available, else gradient + waveform */}
<div
  onClick={handleVisualTap}
  style={{
    height: '100%',
    minHeight: 400,
    cursor: 'pointer',
    position: 'relative',
    overflow: 'hidden',
    background: clip.cover_image ? 'transparent' : `linear-gradient(135deg, ${c}10 0%, ${c}22 50%, #121416 100%)`
  }}
>
  {clip.cover_image ? (
    <img
      src={clip.cover_image}
      alt={clip.title}
      style={{
        width: '100%',
        height: '100%',
        objectFit: 'cover',
        objectPosition: 'center',
      }}
    />
  ) : (
    <>
      {/* Existing gradient background circles */}
      <div style={{...}} />
      <div style={{...}} />
      <div style={{...}} />
      
      {/* Existing waveform visualization in background */}
      <div style={{...}}>...</div>
    </>
  )}
  
  {/* Play/Pause overlay, Category badge, Glassmorphism metadata — unchanged */}
</div>
```

**Logic**:
- If `clip.cover_image` exists: render `<img>` filling the visual area
- Else: render existing gradient background + decorative waveform
- WaveformBar (bottom progress bar) remains unchanged — always shown

### Phase 4: Cleanup & Polish

#### 4.1 Remove CDN hls.js Fallback
**File**: `src/stores/player.tsx:349-363`

Delete the entire `useEffect` that loads hls.js from CDN:
```typescript
// REMOVE THIS:
useEffect(() => {
  if (!window.Hls) {
    hlsReadyRef.current = new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/hls.js@latest/dist/hls.min.js";
      // ...
    });
  }
  return kill;
}, [kill]);
```

Since we import `Hls from 'hls.js'` at the top, it's always available.

#### 4.2 Remove Redundant Auth Checks
**File**: `src/app/AppShell.tsx:13-14`

```tsx
// Remove this — router already guards with RequireAuth
// const { authed } = useAuth();
// if (!authed) return null; // Not needed
```

**File**: `src/app/router.tsx` — Keep only `RequireAuth` wrapper, remove `Protected` (duplicate).

#### 4.3 Fix TypeScript Types
**File**: `src/app/router.tsx:17,72`

```tsx
// Before
function RequireAuth({ children }: { children: JSX.Element }) {
function Protected({ children }: { children: JSX.Element }) {

// After
function RequireAuth({ children }: { children: React.ReactNode }) {
function Protected({ children }: { children: React.ReactNode }) {
```

#### 4.4 Fix ESLint Exhaustive-Deps
**File**: `src/pages/Feed.tsx:63`

```tsx
// Memoize load function
const load = useCallback(async (isInitial = false) => {
  // ... existing logic
}, []); // Empty deps — load doesn't use any external values

useEffect(() => { load(true); }, [load]);
```

---

## 5. Data Flow Changes Summary

### 5.1 Navigation Flow

| Aspect | Before | After |
|--------|--------|-------|
| Navigation API | `go(path, params?)` prop | `useNavigation()` hook → `go(path, params?)` |
| Implementation | `window.location.href` | `react-router` `navigate()` |
| Page Reload | Yes (every click) | No (SPA) |
| State Preservation | Lost on every nav | Preserved |
| URL Params | Query string only | Query string + `location.state` |

### 5.2 HLS Playback Flow

| Aspect | Before | After |
|--------|--------|-------|
| Token Required | Yes (blocks on failure) | Best-effort (fallback on failure) |
| Unmoderated Clips | Broken (403 = no playback) | Works (direct HLS load) |
| hls.js Source | CDN script (async, race) | NPM bundle (sync, reliable) |
| Error Handling | Silent fail | Warning log + fallback |

### 5.3 Visual Rendering Flow

| Aspect | Before | After |
|--------|--------|-------|
| Background | Gradient + decorative bars | Cover image OR gradient + bars |
| WaveformBar | Always shown | Always shown (progress bar) |
| Cover Image | Not supported | Optional, from backend |

### 5.4 Demo Mode Flow

| Aspect | Before | After |
|--------|--------|-------|
| State | Module variable | React Context |
| Reactivity | Manual `setBackendStatus` | Auto via `useBackendStatus` hook |
| Switching | One-way (live→demo) | Bidirectional |

---

## 6. Alternative Approaches Considered

### 6.1 Navigation Alternatives

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **React Router `useNavigate` (chosen)** | Native SPA, preserves state, standard | Requires context/provider | ✅ Selected |
| Keep `window.location.href` | Simple, no changes | Loses all React state on nav | ❌ Rejected |
| Custom history listener | Full control | Reinvents router, complex | ❌ Rejected |
| Hash-based routing | No server config needed | Ugly URLs, SEO issues | ❌ Rejected |

### 6.2 HLS Token Fallback Alternatives

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **Try token, fallback to direct (chosen)** | Resilient, matches main.jsx | Slightly more code | ✅ Selected |
| Require token always | Secure, simple | Breaks for unmoderated clips | ❌ Rejected |
| Pre-check moderation status | Clean separation | Extra API call, race condition | ❌ Rejected |
| Proxy HLS through backend | Centralized auth | Adds latency, complexity | ❌ Rejected |

### 6.3 Cover Image Alternatives

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **Backend model field (chosen)** | Persistent, queryable, standard | Requires migration | ✅ Selected |
| Generate from audio (FFmpeg) | No user upload needed | CPU intensive, not always meaningful | ❌ Rejected |
| Use creator avatar as fallback | Zero backend changes | Not clip-specific | ⚠️ Fallback only |
| External image URL field | Flexible | No validation, broken links | ❌ Rejected |

---

## 7. Impact Analysis

### 7.1 Files to Modify

#### New Files
1. `src/context/NavigationContext.tsx` — Navigation provider + hook
2. `src/context/DemoModeContext.tsx` — Demo mode provider + hook

#### Modified Files (Core)
3. `src/main.tsx` — Add NavigationProvider, DemoModeProvider to provider tree
4. `src/app/router.tsx` — Remove navTo, add NavigationProvider wrapper, fix types
5. `src/app/AppShell.tsx` — Remove redundant auth check
6. `src/stores/player.tsx` — Add HLS token fallback, remove CDN hls.js loader
7. `src/data/feedAdapter.ts` — Remove module-level demoMode, adapt to context
8. `src/components/feed/ReelList.tsx` — Fix 100vh layout bug

#### Modified Files (Pages — all use `useNavigation()`)
9. `src/pages/Feed.tsx`
10. `src/pages/Explore.tsx`
11. `src/pages/Profile.tsx` (also fix stale closure)
12. `src/pages/Inbox.tsx`
13. `src/pages/Library.tsx`
14. `src/pages/Upload.tsx` (add cover image upload)
15. `src/pages/Settings.tsx`
16. `src/pages/Login.tsx`
17. `src/pages/DeveloperDemo.tsx`

#### Modified Files (Components)
18. `src/components/audio/ReelCard.tsx` — Conditional cover image rendering
19. `src/types/index.ts` — Add `cover_image` to AudioClip

#### Backend Files
20. `backend/app/models.py` — Add `cover_image` field to AudioClip
21. `backend/app/serializers.py` — Add `cover_image` to FeedClipSerializer
22. Backend migration file (auto-generated)

### 7.2 API/Interface Changes

| Interface | Change | Breaking? |
|-----------|--------|-----------|
| `AudioClip` type | Add optional `cover_image` | No (optional) |
| `PlayerState` | No change | No |
| `NavigationContext` | New context | N/A (new) |
| `DemoModeContext` | New context | N/A (new) |
| Backend `/feed/` response | Add `cover_image` field | No (optional) |
| Backend `/clips/` upload | Accept `cover_image` multipart | No (optional) |

### 7.3 Concurrency & Error Handling

- **Navigation**: `useNavigate` handles concurrent navigation gracefully (last call wins)
- **HLS Fallback**: Token request and HLS load are sequential; network errors caught
- **Cover Image**: Optional field — no validation errors if missing
- **Demo Mode**: Context updates trigger re-renders; no race conditions

### 7.4 Deployment Considerations

- Backend migration for `cover_image` field must run before frontend deploy
- No changes to Docker/nginx config needed
- No new environment variables required
- HLS token endpoint already exists in backend

---

## 8. Edge Cases & Failure Modes

### 8.1 Navigation Edge Cases

| Scenario | Behavior | Mitigation |
|----------|----------|------------|
| Rapid clicks on nav | `useNavigate` queues/overwrites | React Router handles natively |
| Direct URL access (refresh) | Router matches, renders page | `RequireAuth` checks sessionStorage |
| Deep link to `/profile/123` | `useParams()` reads `userId` | ProfilePage uses `useParams()` |
| Browser back/forward | History API works | Standard React Router behavior |

### 8.2 HLS Playback Edge Cases

| Scenario | Behavior | Mitigation |
|----------|----------|------------|
| Token 403 (unmoderated) | Fallback to direct load | Works if clip is public/accessible |
| Token 404 (clip deleted) | Fallback fails, error shown | `setError('No stream available')` |
| Network error on token | Fallback to direct load | Same as above |
| Safari (native HLS) | Token cookie sent automatically | `credentials: 'include'` in mediaAPI |
| hls.js load error | Error boundary catches | `error` state in PlayerContext |

### 8.3 Cover Image Edge Cases

| Scenario | Behavior | Mitigation |
|----------|----------|------------|
| No cover uploaded | Show gradient + waveform | Current behavior preserved |
| Cover image URL broken | `onError` → hide img, show gradient | Add `onError` handler to `<img>` |
| Large cover image | CSS `object-fit: cover` handles | No layout shift |
| Backend returns null | Conditional renders gradient | TypeScript `cover_image?` handles |

### 8.4 Demo Mode Edge Cases

| Scenario | Behavior | Mitigation |
|----------|----------|------------|
| Backend comes online | `useBackendStatus` fires, context updates | Reactive — no reload needed |
| Backend goes offline | Context switches to demo | Graceful degradation |
| Initial load (backend slow) | Starts in demo, switches when ready | `useBackendStatus` 3s timeout |

---

## 9. Testing Strategy

### 9.1 Existing Tests (Backend)
The backend test suite (`backend/app/tests/`) covers:
- Auth registration/login (including consent)
- Feed generation and suggestions
- HLS token issuance and validation
- Interactions (like, skip, telemetry)
- Comments, shares, follows
- Profile operations
- Pgvector/HNSW index behavior
- HTTPS termination
- Concurrency and adversarial cases

**Gap**: No frontend tests currently exist in the repository.

### 9.2 Required Frontend Tests (New)

| Test Area | Type | Description |
|-----------|------|-------------|
| Navigation | Integration | Click avatar → profile loads without reload |
| Navigation | Integration | Back button preserves player state |
| Auth Flow | Integration | Register → onboarding modal → feed |
| Auth Flow | Integration | Login → redirect to feed (no reload) |
| HLS Playback | Unit | Token success → HLS loads |
| HLS Playback | Unit | Token 403 → direct load attempted |
| HLS Playback | Unit | Token network error → direct load attempted |
| Cover Image | Unit | Clip with cover → image renders |
| Cover Image | Unit | Clip without cover → gradient + waveform |
| Cover Image | Integration | Upload with cover → appears in feed |
| Demo Mode | Integration | Backend offline → demo data shown |
| Demo Mode | Integration | Backend online → live data shown |
| ReelList | Visual | Items snap correctly, no double height |
| Player | Integration | Play → pause → seek → next auto-advance |

### 9.3 Test Commands

```bash
# Backend tests (existing)
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build -d
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/ --tb=short

# Frontend lint + build
cd frontend/sample_frontend
npm run lint
npm run build

# Frontend dev server (manual testing)
npm run dev
```

### 9.4 Validation Checklist

- [ ] Login → feed navigation without reload
- [ ] Feed → profile → back to feed preserves player
- [ ] HLS playback works for moderated clips
- [ ] HLS playback works for unmoderated clips (fallback)
- [ ] Cover image upload + display in feed
- [ ] WaveformBar shown when no cover image
- [ ] Demo mode shows demo data when backend down
- [ ] Live mode shows real data when backend up
- [ ] ReelList scroll/snap works correctly
- [ ] BottomNav unread count updates
- [ ] Theme toggle persists
- [ ] Onboarding modal shows after register

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| React Router version mismatch | Low | High | Verify `react-router-dom` v6 in package.json |
| Backend migration fails | Low | High | Test migration in staging first |
| Cover image upload too large | Medium | Medium | Add client-side size validation (5MB) |
| HLS fallback breaks on some browsers | Low | Medium | Test Chrome, Firefox, Safari |
| Navigation context not available | Low | High | Add error boundary + clear error message |
| Demo mode flicker on load | Medium | Low | Show skeleton while backend check runs |
| TypeScript errors after refactor | Medium | Medium | Run `tsc --noEmit` after each phase |

---

## 11. Decisions & Constraints Established

| Decision | Constraint | Notes |
|----------|------------|-------|
| SPA navigation required | Must use `useNavigate` | No full-page reloads |
| HLS token kept | Must add fallback | Backend security model preserved |
| Cover image optional | No breaking changes | `cover_image?: string \| null` |
| NPM hls.js only | Remove CDN logic | Simplifies player.tsx |
| Terracotta/Sage theme | No design changes | sample_frontend is source of truth |
| AppShell pattern | Keep shared shell | All pages use AppShell |

---

## 12. Implementation Order (Sequential Dependencies)

```
Phase 1: Navigation Context
    │
    ├── 1.1 Create NavigationContext.tsx
    ├── 1.2 Update main.tsx provider tree
    ├── 1.3 Refactor router.tsx (remove navTo, add provider)
    ├── 1.4 Update all pages to use useNavigation()
    │
    ├── 1.5 Create DemoModeContext.tsx
    ├── 1.6 Update feedAdapter.ts + components
    │
Phase 2: Critical Bug Fixes (can parallelize after Phase 1)
    │
    ├── 2.1 Fix ProfilePage stale closure
    ├── 2.2 Add HLS token fallback in player.tsx
    ├── 2.3 Fix ReelList 100vh layout
    ├── 2.4 Remove CDN hls.js loader
    ├── 2.5 Remove redundant auth checks
    ├── 2.6 Fix TypeScript JSX.Element types
    ├── 2.7 Fix ESLint exhaustive-deps
    │
Phase 3: Cover Image Feature
    │
    ├── 3.1 Backend: Add cover_image to AudioClip model + migration
    ├── 3.2 Backend: Add cover_image to FeedClipSerializer
    ├── 3.3 Frontend: Add cover_image to AudioClip type
    ├── 3.4 Frontend: Update UploadPage for cover upload
    ├── 3.5 Frontend: Update ReelCard conditional rendering
    │
Phase 4: Validation
    │
    ├── 4.1 Run lint + build
    ├── 4.2 Manual E2E testing
    ├── 4.3 Backend tests still pass
```

---

## 13. Appendix: Code References

### 13.1 Key Functions to Modify

| Function | File | Lines | Change Type |
|----------|------|-------|-------------|
| `navTo` | `router.tsx` | 52-55 | **Remove** |
| `loadSource` | `player.tsx` | 44-82 | **Modify** (add fallback) |
| `fetchFeed` | `feedAdapter.ts` | 18-50 | **Modify** (use context) |
| `load` | `Feed.tsx` | 25-61 | **Modify** (memoize + useNavigation) |
| `ProfilePage` | `Profile.tsx` | 32-60 | **Fix** (stale closure) |
| `ReelList` render | `ReelList.tsx` | 70-105 | **Fix** (height calc) |
| `ReelCard` visual | `ReelCard.tsx` | 94-172 | **Modify** (conditional image) |

### 13.2 New Exports Needed

```typescript
// src/context/NavigationContext.tsx
export { NavigationProvider, useNavigation };

// src/context/DemoModeContext.tsx  
export { DemoModeProvider, useDemoMode };
```

### 13.3 Provider Tree (main.tsx)

```tsx
<ThemeProvider>
  <ToastProvider>
    <AuthProvider>
      <NavigationProvider>        {/* NEW */}
        <DemoModeProvider>        {/* NEW */}
          <PlayerProvider>
            <AppRouter />
          </PlayerProvider>
        </DemoModeProvider>
      </NavigationProvider>
    </AuthProvider>
  </ToastProvider>
</ThemeProvider>
```

---

## 14. Conclusion

This plan addresses all 18 identified issues across 4 phases, transforming the sample_frontend from a broken prototype with full-page reloads and missing features into a production-ready SPA that matches the canonical main.jsx behavior while adding cover image support and improved resilience.

**Next Steps**: Begin Phase 1 implementation with `NavigationContext.tsx` and provider tree updates.

---