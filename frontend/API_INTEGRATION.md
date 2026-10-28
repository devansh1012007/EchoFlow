# API integration

Every backend route a person can reach from the web app, the UI that calls it,
and the test that pins the request. Paths are taken from `src/api/client.ts` and
the call sites in `src/`; if this file and the code disagree, the code wins.

All requests go through `apiRequest` in `src/api/client.ts`, which attaches the
bearer token, refreshes it once on a 401, and raises `ApiError` with a `kind`
(`http`, `network`, `timeout`). `src/api/messages.ts` (`describeApiError`) turns
that into one sentence a person can act on. The client attaches a token only
when a refresh token is also stored.

## Signed-out

| Route | UI | Tests |
|---|---|---|
| `POST /auth/register/` | Login → register | `login.test.tsx`, `ageGate.boundary.test.tsx` |
| `POST /auth/login/` | Login | `login.test.tsx` |
| `GET /legal/compliance/` | Login (terms version); Support (officer contacts) | `login.test.tsx`, `support.test.tsx` |
| `POST /grievance/` | `/support` → Report a problem | `support.test.tsx`, `clientEndpoints.test.ts` |
| `POST /legal/takedown/` | `/support` → Request removal of a clip | `support.test.tsx`, `clientEndpoints.test.ts` |
| `GET /clips/{id}/public/` | `/clip/:id?s=…` | `sharedClip.test.tsx`, `clientEndpoints.test.ts` |
| `POST /clips/{id}/play/` | `/clip/:id?s=…` → Play (credentials included for the `/hls/` cookie) | `sharedClip.test.tsx`, `clientEndpoints.test.ts` |
| `GET /health/`, `GET /ready/` | Header status (both probes must pass for "Backend Ready") | `backendHealth.test.tsx` |

## Signed-in

| Route | UI | Tests |
|---|---|---|
| `POST /auth/logout/` | sign out (`stores/auth.tsx`) | `clientEndpoints.test.ts` |
| `POST /auth/token/refresh/` | silent refresh inside `apiRequest` | `client.test.tsx` |
| `GET /profile/me/`, `PATCH /profile/me/update/` | Profile | `profile.test.tsx`, `client.test.tsx` |
| `GET /profile/{id}/`, `GET /profile/{id}/clips/` | Profile (other users) | `profile.test.tsx` |
| `GET /feed/` | Feed | `feed.test.tsx` |
| `GET /suggestions/?category=` | Explore | `explore.test.tsx` |
| `GET /tags/available/`, `POST /tags/initialize/` | Onboarding | `onboardingModal.test.tsx` |
| `POST /clips/` | Upload | `upload.test.tsx` |
| `GET /clips/{id}/`, `POST /clips/{id}/approve-moderation/`, `GET /subscription/` | Upload (status polling, moderation, limits) | `upload.test.tsx` |
| `GET /clips/?page=` | Profile → Uploads in progress (follows pages, capped at 5) | `uploadsPanel.test.tsx` |
| `PATCH /clips/{id}/`, `DELETE /clips/{id}/` | Profile (own clips) | `profile.test.tsx` |
| `GET /clips/{id}/resolve/` | `?clip=` deep link in `App.tsx` | `appShell.test.tsx` |
| `POST /clips/{id}/report/` | Flag button on a reel → Report dialog | `reportDialog.test.tsx`, `reelCard.test.tsx` |
| `POST /clips/{id}/share-link/` | Profile → link button on your own clip | `shareLinkDialog.test.tsx` |
| `POST /interactions/{id}/toggle-like/` | Reel like button | `reelCard.test.tsx`, `client.test.tsx` |
| `POST /interactions/{id}/register-skip/`, `…/log-telemetry/` | Player (`stores/player.tsx`) | `playerStore.test.tsx` |
| `GET/POST /comments/`, `PATCH/DELETE /comments/{id}/` | Comment sheet (edit is inline, own comments only) | `commentSheet.test.tsx` |
| `POST /follow/{id}/toggle-follow/` | Reel follow button | `reelCard.test.tsx` |
| `GET /share/find-user/`, `POST /share/{id}/send-share/` | Share dialog | `shareModal.test.tsx`, `client.test.tsx` |
| `GET /share/inbox/`, `GET /share/unread-count/`, `POST /share/{id}/mark-read/`, `DELETE /share/{id}/share-delete/` | Inbox, nav badge | `inbox.test.tsx` |
| `POST /media/playback-token/{id}/` | Player | `playerStore.test.tsx`, `client.test.tsx` |
| `GET /subscription/`, `POST /subscription/sync/`, `GET /subscription/manage/` | Profile → Plan | `accountPanel.test.tsx`, `clientEndpoints.test.ts` |
| `GET /data-subject/access/` | Profile → Your data → View my data | `accountPanel.test.tsx` |
| `POST /data-subject/erasure/` | Profile → Your data → Delete account | `accountPanel.test.tsx` |

## Not called from the web app, on purpose

- RevenueCat webhook: server to server.
- `/metrics/`, `/admin/`, `GET /api/v1/health/media-worker/`: operations.

## Things worth knowing

- **Account deletion is two-stage.** The first `POST /data-subject/erasure/`
  opens a 30-day cooling-off and deletes nothing. A second request *after* the
  cooling-off starts real, irreversible deletion. There is no cancel endpoint.
  The dialog states both facts and requires typing `DELETE`; during the
  cooling-off the server answers 403 with `cooling_off_until`, which the UI
  shows as the current state, not as an error.
- **Own-upload visibility.** `GET /profile/{id}/clips/` returns only clips that
  are `ready` *and* moderation-approved, even to the owner. `GET /clips/`
  returns every state but its serializer omits `moderation_approved`, so a
  `ready` clip waiting for approval is in neither list. Exposing that flag would
  be a backend change.
- **Share links.** `POST /clips/{id}/share-link/` is creator-only and returns a
  relative `/clip/{id}?s=…` unless `PUBLIC_APP_BASE_URL` is set on the server;
  the UI then builds an absolute link from the current origin. The `/clip/:id`
  page is served by the SPA fallback in `public/_redirects`.
- **Verification level.** The request shape of every route above is checked
  against the Django views by reading them, and by the contract tests in
  `clientEndpoints.test.ts`. None of it has been run against a live backend.
