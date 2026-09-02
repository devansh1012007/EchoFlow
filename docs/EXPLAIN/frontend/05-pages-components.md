# Frontend Pages & Components

## Page Components

### FeedPage (`pages/Feed.tsx`)

**Main feed consumption page.**

```typescript
const [clips, setClips] = useState<AudioClip[]>([]);
const [loading, setLoading] = useState(true);
const [hasMore, setHasMore] = useState(true);

const load = async (isInitial = false) => {
  const { clips: fresh, hasMore: hm, err } = await fetchFeed();
  setClips(prev => [...prev, ...fresh]);
  setHasMore(hm);
};

useEffect(() => { load(true); }, []);
```

**Features:**
- Infinite scroll via `ReelList` sentinel
- Pull-to-refresh not implemented
- "Caught up" state when `hasMore=false`
- Demo mode banner

### ReelList (`components/feed/ReelList.tsx`)

**Virtualized feed container with IntersectionObserver.**

```typescript
// Infinite scroll sentinel
const sentinelRef = useRef<HTMLDivElement | null>(null);
useEffect(() => {
  if (!sentinelRef.current || !loadMore) return;
  const obs = new IntersectionObserver(([e]) => {
    if (e.isIntersecting && hasMore && !loading) loadMore();
  }, { threshold: 0.1 });
  obs.observe(sentinelRef.current);
  return () => obs.disconnect();
}, [clips.length, hasMore, loading, loadMore]);
```

**Auto-advance:**
```typescript
// Scroll to next reel after completion
useEffect(() => {
  if (active && progress >= 0.99) {
    const idx = clips.findIndex(c => c.id === active.id);
    if (idx >= 0 && idx < clips.length - 1) {
      const timer = setTimeout(() => {
        itemRefs.current[idx + 1]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 1000);
      return () => clearTimeout(timer);
    }
  }
}, [active, progress, clips]);
```

**Auto-play on view:**
```typescript
const obs = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      const idx = Number(entry.target.dataset.index);
      const clip = clips[idx];
      if (activeIdRef.current !== clip.id) play(clip);
    }
  });
}, { threshold: 0.6 });
```

### ReelCard (`components/audio/ReelCard.tsx`)

**Full-screen clip card with all interactions.**

**Visual Structure:**
```
┌─────────────────────────────────────┐
│  Ambient gradient background        │
│  ┌─────────────────────────────┐    │
│  │   Category badge            │    │
│  └─────────────────────────────┘    │
│                                     │
│  ┌─────────────────────────────┐    │
│  │   Play/Pause overlay        │    │
│  │   (shows on double-tap)     │    │
│  └─────────────────────────────┘    │
│                                     │
│  ┌─────────────────────────────┐    │
│  │   Waveform bars (static)    │    │
│  └─────────────────────────────┘    │
│                                     │
│  Glass metadata overlay (bottom)    │
│  ├─ Avatar + username + follow btn  │
│  ├─ Title                           │
│  ├─ Tags (#genre)                   │
│  └─ Duration + like count           │
│                                     │
│  WaveformBar (animated)             │
└─────────────────────────────────────┘
┌─────────────────────────────────────┐
│  Right-side action stack            │
│  ├─ ♥ Like (ripple animation)       │
│  ├─ 💬 Comments (opens CommentSheet)│
│  ├─ ⤴ Share (opens ShareModal)      │
│  ├─ ⏭ Skip +10s                     │
│  └─ ⏮ Skip -10s                     │
└─────────────────────────────────────┘
```

**Interactions:**
```typescript
// Double-tap detection (250ms window)
const handleVisualTap = (e) => {
  if (tapTimeout.current) {
    clearTimeout(tapTimeout.current);
    setShowPlayIcon(true);
    setTimeout(() => setShowPlayIcon(false), 600);
    if (isActive) { if (!globalPlaying) play(clip); }
    return;
  }
  tapTimeout.current = setTimeout(() => { play(clip); }, 250);
};

// Like toggle with optimistic update
const toggleLike = async () => {
  setLiked(!liked);
  setLikes(n => liked ? n + 1 : n - 1);
  try { await interactionsAPI.toggleLike(clip.id); }
  catch { rollback optimistic update; }
};

// Follow creator
const toggleFollow = async () => {
  setFollowing(!following);
  try { await followAPI.toggleFollow(creatorId); }
  catch { rollback; }
};
```

### ExplorePage (`pages/Explore.tsx`)

**Category-browsing page.**

```typescript
const [category, setCategory] = useState('music');
const [clips, setClips] = useState<AudioClip[]>([]);

const load = async () => {
  const data = await feedAPI.getSuggestions(category);
  setClips(data.results || data);
};
```

**Features:**
- Category tabs (music, comedy, education, etc.)
- Horizontal scroll category selector
- Uses `SuggestionViewSet` (category-scoped vector ranking)

### ProfilePage (`pages/Profile.tsx`)

**Own profile + public profile view.**

```typescript
// Own profile: /profile/me/
const { data } = await profileAPI.getMyProfile();
// Shows: liked_clips (last 50), uploads, followers/following counts

// Public profile: /profile/{userId}/
const { data } = await profileAPI.getProfile(userId);
// Shows: uploads, followers/following counts
```

**Tabs:** Clips | Likes | Followers | Following

### InboxPage (`pages/Inbox.tsx`)

**Share inbox with unread count.**

```typescript
const [shares, setShares] = useState<ShareEvent[]>([]);
const [unread, setUnread] = useState(0);

useEffect(() => {
  const data = await shareAPI.getInbox();
  setShares(data);
  setUnread(data.filter(s => !s.is_read).length);
}, []);
```

**Features:**
- Mark read on open
- Delete from inbox (doesn't undo share)
- Navigate to clip via ReelCard

### LibraryPage (`pages/Library.tsx`)

**User's liked clips.**

```typescript
const { data } = await profileAPI.getMyProfile();
// data.liked_clips → FeedResponse
```

### UploadPage (`pages/Upload.tsx`)

**Clip upload with metadata.**

```typescript
const [title, setTitle] = useState('');
const [category, setCategory] = useState('');
const [file, setFile] = useState<File | null>(null);

const handleSubmit = async () => {
  const fd = new FormData();
  fd.append('title', title);
  fd.append('category', category);
  fd.append('original_file', file);
  const { clip_id } = await clipsAPI.uploadClip(fd);
  // Poll for status or navigate to feed
};
```

**Validation:** Client-side file type/size check (mirrors serializer)

### SettingsPage (`pages/Settings.tsx`)

**User settings:**
- Profile picture upload
- Username change
- Theme toggle (dark only currently)
- Logout button

### LoginPage (`pages/Login.tsx`)

**Auth page with register/login toggle.**

```typescript
const [isLogin, setIsLogin] = useState(true);
const [form, setForm] = useState({username: '', password: '', email: ''});

const handleSubmit = async () => {
  if (isLogin) await auth.login(form.username, form.password);
  else await auth.register(form.email, form.username, form.password);
  onSuccess();  // Navigate to feed/explore
};
```

### DeveloperDemoPage (`pages/DeveloperDemo.tsx`)

**Demo mode showcase with mock data.**

---

## Common Components

### BottomNav (`components/navigation/BottomNav.tsx`)

**Fixed bottom tab bar:**
```typescript
const tabs = [
  { id: 'feed', icon: Home, label: 'Feed' },
  { id: 'explore', icon: Compass, label: 'Explore' },
  { id: 'inbox', icon: Inbox, label: 'Inbox', badge: unread },
  { id: 'profile', icon: User, label: 'Profile' },
];
```

### CommentSheet (`components/comments/CommentSheet.tsx`)

**Bottom sheet modal for comments:**
- Flat list (top-level only)
- Reply not implemented in UI
- Post new comment
- Real-time like count

### ShareModal (`components/sharing/ShareModal.tsx`)

**Share to user modal:**
- Search user by username
- Recent shares shown
- Send share → `shareAPI.sendShare()`

### WaveformBar (`components/audio/WaveformBar.tsx`)

**Animated waveform visualization:**
```typescript
// Generates static bars based on clip.duration_ms
// Animates height based on playback progress
// Purely visual — not real audio data
```

### Atoms (`components/common/atoms.tsx`)

**Reusable primitives:**
- `Avatar` — username initials + color
- `CatBadge` — category pill with color
- `Button` — styled button variants

### Molecules (`components/common/molecules.tsx`)

**Composite components:**
- `FeedSkeleton` — loading placeholders
- `ErrorBox` — error with retry button

---

## Navigation Flow

```
AppRouter (BrowserRouter)
├── /login → LoginPage
├── /feed → FeedPage → ReelList → ReelCard
├── /explore → ExplorePage → ReelList → ReelCard
├── /profile/me → ProfilePage (own)
├── /profile/:userId → ProfilePage (public)
├── /inbox → InboxPage → ShareEvent list
├── /library → LibraryPage → liked clips
├── /upload → UploadPage
├── /settings → SettingsPage
└── /dev/demo → DeveloperDemoPage
```

**Auth Guard:**
```typescript
function RequireAuth({ children }) {
  const { authed } = useAuth();
  if (!authed) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
```

---

## Data Fetching Patterns

| Page | Hook | Endpoint | Caching |
|------|------|----------|---------|
| FeedPage | `useEffect` + `fetchFeed` | `/feed/` | None (Redis-backed) |
| ExplorePage | `useEffect` | `/suggestions/?category=` | None |
| ProfilePage | `useEffect` | `/profile/me/` or `/profile/{id}/` | None |
| InboxPage | `useEffect` | `/share/inbox/` | None |
| LibraryPage | `useEffect` | `/profile/me/` (liked_clips) | None |

**No React Query / SWR** — manual `useEffect` + state.

---

*Source: `frontend/sample_frontend/src/pages/*.tsx`, `frontend/sample_frontend/src/components/**/*.tsx`*