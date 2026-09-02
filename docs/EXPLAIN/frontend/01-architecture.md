# Frontend Architecture

## Overview

**Location:** `frontend/sample_frontend/` — Vite + React + TypeScript sample client

**Stack:**
- React 18 + TypeScript
- Vite 7 (dev server, build)
- React Router 6 (routing)
- HLS.js (audio playback)
- Tailwind CSS (styling)
- Lucide React (icons)

**State Management:** React Context providers (no Redux/Zustand)

---

## Component Hierarchy

```
AppRouter (BrowserRouter)
├── AuthProvider
├── PlayerProvider
├── ToastProvider
├── ThemeProvider
│
├── Routes
│   ├── /login → LoginPage
│   ├── /feed → FeedPage (RequireAuth)
│   ├── /explore → ExplorePage (RequireAuth)
│   ├── /profile/:userId? → ProfilePage (RequireAuth)
│   ├── /inbox → InboxPage (RequireAuth)
│   ├── /library → LibraryPage (RequireAuth)
│   ├── /upload → UploadPage (RequireAuth)
│   ├── /settings → SettingsPage (RequireAuth)
│   └── /dev/demo → DeveloperDemoPage (RequireAuth)
│
├── AppShell (wraps authenticated pages)
│   ├── NetworkBanner (connectivity status)
│   ├── Page content (FeedPage, ExplorePage, etc.)
│   ├── MiniPlayer (persistent audio player)
│   ├── BottomNav (navigation)
│   └── OnboardingModal (new user tag selection)
│
└── BackendWatcher (monitors backend health)
```

---

## Data Flow

### Authentication Flow
```
LoginPage → authAPI.login() → setTokens() → AuthProvider.login()
    │
    ├── sessionStorage: ef_access, ef_refresh, ef_user
    ├── sessionStorage: ef_new_user = '1' (triggers onboarding)
    ▼
AppRouter RootRedirect → /feed (if authed)
```

### Feed Loading Flow
```
FeedPage → fetchFeed() → feedAPI.getFeed() → /feed/
    │
    ├── Demo mode: returns mock data from data/feedAdapter
    └── Production: FastFeedViewSet → Redis LPOP → FeedClipSerializer
    │
    ▼
FeedPage.setClips() → ReelList → ReelCard (per clip)
    │
    ├── ReelCard handles: play, like, comment, share, follow, skip
    └── PlayerProvider manages actual audio playback
```

### Playback Flow
```
ReelCard.play(clip) → PlayerProvider.play(clip)
    │
    ├── loadSource(clip) → Hls.js or native HLS
    ├── audioRef.current.play()
    ├── IntersectionObserver auto-plays visible reel
    ├── onEnded → auto-advance to next reel
    └── onUnmount → logTelemetry(watch_time_ms)
```

---

## Project Structure

```
frontend/sample_frontend/
├── src/
│   ├── main.tsx                 # App entry, provider composition
│   ├── app/
│   │   ├── router.tsx           # Routes, auth guards, navigation
│   │   └── AppShell.tsx         # Page wrapper, mini player, bottom nav
│   ├── api/
│   │   └── client.ts            # API client, token management, endpoints
│   ├── stores/
│   │   ├── auth.tsx             # AuthContext: user, tokens, login/logout
│   │   ├── player.tsx           # PlayerContext: HLS.js, playback controls
│   │   ├── theme.tsx            # ThemeContext: dark/light/system
│   │   └── toast.tsx            # ToastContext: notifications
│   ├── components/
│   │   ├── audio/
│   │   │   ├── ReelCard.tsx     # Main clip UI: visual, actions, waveform
│   │   │   └── WaveformBar.tsx  # Animated waveform visualization
│   │   ├── comments/
│   │   │   └── CommentSheet.tsx # Bottom sheet for comments
│   │   ├── common/
│   │   │   ├── atoms.tsx        # Avatar, CatBadge, Button
│   │   │   └── molecules.tsx    # FeedSkeleton, ErrorBox
│   │   ├── feed/
│   │   │   ├── ReelList.tsx     # Virtualized feed list, intersection observer
│   │   │   ├── MiniPlayer.tsx   # Persistent bottom player bar
│   │   │   └── OnboardingModal.tsx # Tag selection for new users
│   │   ├── navigation/
│   │   │   └── BottomNav.tsx    # Bottom tab bar
│   │   └── sharing/
│   │       └── ShareModal.tsx   # Share to user modal
│   ├── pages/
│   │   ├── Feed.tsx             # Main feed page
│   │   ├── Explore.tsx          # Category explore page
│   │   ├── Profile.tsx          # User profile (own + public)
│   │   ├── Inbox.tsx            # Share inbox
│   │   ├── Library.tsx          # Liked clips
│   │   ├── Upload.tsx           # Clip upload UI
│   │   ├── Settings.tsx         # User settings
│   │   ├── Login.tsx            # Login/register
│   │   └── DeveloperDemo.tsx    # Demo mode showcase
│   ├── hooks/
│   │   └── useBackendStatus.ts  # Polls /health/ for connectivity
│   ├── data/
│   │   ├── feedAdapter.ts       # Demo/production feed fetching
│   │   └── clips.ts             # Category colors, mock data
│   ├── types/
│   │   └── index.ts             # TypeScript interfaces
│   └── styles/
│       └── globals.css          # Tailwind + CSS variables
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.js
└── .eslintrc.cjs
```

---

## Key Design Decisions

### 1. Context Providers Over Global State Library
- **Why:** Lightweight, no extra dependencies, sufficient for current scope
- **Trade-off:** No devtools, potential re-render optimization needed

### 2. Persistent MiniPlayer
- **Why:** Audio continues playing during navigation (TikTok-style)
- **Implementation:** `PlayerProvider` at root, `MiniPlayer` in `AppShell`

### 3. IntersectionObserver for Auto-Play
- **Why:** Play visible reel, pause others — no manual play button needed
- **Implementation:** `ReelList` observes `ReelCard` refs

### 4. Demo Mode Fallback
- **Why:** Frontend runnable without backend
- **Implementation:** `isDemoMode()` in `feedAdapter`, mock data in `clips.ts`

### 5. HLS.js with Native Fallback
- **Why:** Safari supports native HLS; Chrome/Firefox need HLS.js
- **Implementation:** `PlayerProvider.loadSource()` detects support

---

## TypeScript Interfaces (`src/types/index.ts`)

```typescript
interface User {
  id: number;
  username: string;
  email?: string;
  profile_picture?: string | null;
  followers_count?: number;
  following_count?: number;
  uploads_count?: number;
}

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
}

interface FeedResponse {
  results: AudioClip[];
  next?: string | null;
  queue_health?: number;
  message?: string;
}

interface Comment { ... }
interface ShareEvent { ... }
interface UserProfile { ... }
interface PlayerState { ... }
```

---

## Environment Configuration

```bash
# .env.local
VITE_API_BASE_URL=http://100.124.196.125:8005  # Backend API
```

**Default:** `http://100.124.196.125:8005` (Tailscale IP for dev)

---

## Build & Dev Commands

```bash
cd frontend/sample_frontend
npm install
npm run dev      # Vite dev server on :5173
npm run build    # Production build to dist/
npm run preview  # Preview production build
npm run lint     # ESLint
```

---

## Demo Mode

**Activation:** `isDemoMode()` checks `VITE_API_BASE_URL` or localStorage flag

**Behavior:**
- `FeedPage` shows "DEMO" badge
- `fetchFeed()` returns mock clips from `data/clips.ts`
- `ReelCard` shows visual placeholder (no real audio)
- All interactions work but no API calls

**Use case:** Frontend development without backend running.

---

## Network Resilience

### Backend Health Monitoring
```typescript
// hooks/useBackendStatus.ts
useBackendStatus() → polls /health/ every 10s
    │
    ▼
AppShell → NetworkBanner shows "Offline" indicator
```

### API Error Handling
```typescript
// api/client.ts
api() → 401 → auto-refresh token → retry
     → network error → throw {status: 0, message: 'Network error'}
     → other errors → throw {status, message, errors}
```

### Toast Notifications
```typescript
// stores/toast.tsx
toast(message, type?: 'success'|'error'|'info'|'warn', ms?: number)
```
Auto-dismiss after 4s (configurable).

---

## Styling System

- **Tailwind CSS** + custom CSS variables
- **CSS variables** for theming (defined in `globals.css`):
  ```css
  :root {
    --terracotta: #e07a5f;
    --sage: #8abd9c;
    --bg: #0a0c10;
    --surface: #121416;
    --on-surface: #ffffff;
    --outline: #6b6b6b;
    --radius-full: 9999px;
    --radius-xl: 16px;
    --font-display: 'Space Grotesk', sans-serif;
  }
  ```
- **Dark mode only** (no light mode implementation yet)
- **Scan line overlay** (`.scan-line`) for aesthetic

---

## Performance Considerations

| Technique | Implementation |
|-----------|----------------|
| Virtualized feed | `ReelList` uses `scroll-snap` + IntersectionObserver |
| Image optimization | `Avatar` component with CSS sizing |
| Code splitting | Vite automatic (routes not lazy-loaded yet) |
| Memoization | `useCallback` in `PlayerProvider`, `ReelCard` handlers |
| Bundle analysis | Not configured |

---

*Source: `frontend/sample_frontend/src/` directory structure and source files*