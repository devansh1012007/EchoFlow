# Frontend API Layer

## Overview

**File:** `frontend/sample_frontend/src/api/client.ts`

Centralized API client with:
- Token management (sessionStorage)
- Auto-refresh on 401
- Type-safe endpoint methods
- Error normalization

---

## Token Management

### Storage
```typescript
sessionStorage:
  - ef_access: JWT access token (15 min)
  - ef_refresh: JWT refresh token (7 days)
  - ef_user: Serialized User object
  - ef_new_user: '1' flag for onboarding trigger
```

### Functions
```typescript
setTokens({access, refresh})           // Store tokens + user
clearTokens()                          // Clear all auth data
getAccessToken(): string | null        // For Authorization header
getRefreshToken(): string | null       // For refresh endpoint
dispatchSessionExpired()               // Custom event for logout
```

---

## Core API Function

```typescript
async function api(path: string, opts: RequestInit = {}): Promise<any>
```

**Behavior:**
1. Adds `Authorization: Bearer <access_token>` if available
2. Handles `FormData` (removes Content-Type for multipart)
3. Makes `fetch` to `${API_BASE}${path}`
4. On 401:
   - Tries refresh token via `/auth/token/refresh/`
   - On success: updates tokens, retries original request
   - On failure: clears tokens, dispatches `ef_session_expired`, throws
5. Normalizes errors: throws `{status, message, errors}`

**API_BASE:** `import.meta.env.VITE_API_BASE_URL || 'http://100.124.196.125:8005'`

---

## Endpoint Modules

### Auth (`authAPI`)
```typescript
login(username, password)       → POST /auth/login/
register(email, username, pass) → POST /auth/register/
refresh(refresh)                → POST /auth/token/refresh/
```

### Feed (`feedAPI`)
```typescript
getFeed()                       → GET /feed/ → FeedResponse
getSuggestions(category)        → GET /suggestions/?category= → FeedResponse | AudioClip[]
```

### Clips (`clipsAPI`)
```typescript
uploadClip(FormData)            → POST /clips/ → {clip_id, status}
getUserClips(userId)            → GET /profile/{userId}/clips/ → FeedResponse
```

### Interactions (`interactionsAPI`)
```typescript
toggleLike(clipId)              → POST /interactions/{clipId}/toggle-like/
registerSkip(clipId, data)      → POST /interactions/{clipId}/register-skip/
logTelemetry(clipId, data)      → POST /interactions/{clipId}/log-telemetry/
```

### Comments (`commentsAPI`)
```typescript
getComments(clipId)             → GET /comments/?clip={clipId} → {results: Comment[]}
postComment({clip, text, parent?}) → POST /comments/
deleteComment(commentId)        → DELETE /comments/{commentId}/
```

### Share (`shareAPI`)
```typescript
getInbox()                      → GET /share/inbox/ → ShareEvent[]
getUnread()                     → GET /share/unread-count/ → {unread: number}
findUser(username)              → GET /share/find-user/?username= → {id, username}
sendShare(clipId, receiverId)   → POST /share/{clipId}/send-share/
markRead(id)                    → PATCH /share/{id}/mark-read/
deleteShare(id)                 → DELETE /share/{id}/share-delete/
```

### Follow (`followAPI`)
```typescript
toggleFollow(userId)            → POST /follow/{userId}/toggle-follow/
```

### Profile (`profileAPI`)
```typescript
getMyProfile()                  → GET /profile/me/ → UserProfile
getProfile(userId)              → GET /profile/{userId}/ → UserProfile
updateProfile(FormData)         → PATCH /profile/me/update/
```

### Tags (`tagsAPI`)
```typescript
initialize(tags: string[])      → POST /tags/initialize/ {selected_tags}
```

---

## Media URL Resolution

```typescript
function resolveMediaUrl(url: string | null | undefined): string | null
```

**Logic:**
- Null/undefined → null
- Already absolute (http/https) → return as-is
- Relative → prepend `API_BASE`

**Used for:** Profile pictures, fallback media URLs.

---

## Error Handling

### Thrown Error Shape
```typescript
{
  status: number,        // HTTP status (0 = network error)
  message: string,       // Human-readable message
  errors: any            // Raw error response (validation details, etc.)
}
```

### Common Patterns
```typescript
try {
  const data = await feedAPI.getFeed();
} catch (err) {
  if (err.status === 0) {
    // Network error — backend down?
  } else if (err.status === 401) {
    // Handled by api() — triggers refresh, then logout
  } else if (err.status === 400) {
    // Validation errors in err.errors
  }
  toast(err.message, 'error');
}
```

---

## Session Expiry Handling

```typescript
// AuthProvider listens for custom event
useEffect(() => {
  const handle = () => logout();
  window.addEventListener('ef_session_expired', handle);
  return () => window.removeEventListener('ef_session_expired', handle);
}, [logout]);
```

**Triggers:**
- Refresh token expired/invalid
- Network error during refresh
- Backend returns 401 on refresh attempt

---

## Type Safety

**Response types** defined in `src/types/index.ts`:
- `FeedResponse`, `AudioClip`, `Comment`, `ShareEvent`, `UserProfile`
- `AuthTokens` (access, refresh)

**Request types** inferred from function parameters.

**Gap:** No runtime validation (e.g., zod) — trusts backend schema.

---

## Demo Mode Support

```typescript
// data/feedAdapter.ts
export function isDemoMode(): boolean {
  return import.meta.env.DEV && !import.meta.env.VITE_API_BASE_URL;
}
```

**Behavior:**
- `feedAPI.getFeed()` returns mock data from `data/clips.ts`
- Other endpoints may need demo implementations

---

## Extending the API Client

### Adding New Endpoint
```typescript
// 1. Add type to src/types/index.ts
export interface NewResource { ... }

// 2. Add API function to client.ts
export const newAPI = {
  getAll: () => api('/new-endpoint/'),
  getOne: (id: string) => api(`/new-endpoint/${id}/`),
  create: (data: NewResource) => api('/new-endpoint/', {method: 'POST', body: JSON.stringify(data)}),
};

// 3. Use in component
import { newAPI } from '../api/client';
const data = await newAPI.getAll();
```

---

## Security Considerations

| Aspect | Implementation | Gap |
|--------|---------------|-----|
| Token storage | sessionStorage (cleared on tab close) | XSS accessible |
| Token refresh | Automatic on 401 | No refresh token rotation |
| HTTPS enforcement | None (dev uses HTTP) | Production needs HTTPS |
| Request signing | None | No request integrity |
| Rate limit handling | None | No retry-after respect |

---

*Source: `frontend/sample_frontend/src/api/client.ts`, `frontend/sample_frontend/src/stores/auth.tsx`, `frontend/sample_frontend/src/types/index.ts`*