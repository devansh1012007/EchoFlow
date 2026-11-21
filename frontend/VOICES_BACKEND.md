# Voices: the backend this frontend expects

Voices is live in the app today as a **single-user, on-device** feature: posts
are recorded in the browser and stored in IndexedDB (`src/voices/store.ts`).
Every component talks to the `VoiceBackend` interface in `src/voices/types.ts`
through `src/voices/useVoices.ts` — nothing above that hook knows where a post
lives. This document is the HTTP contract a server implementation should
satisfy so the page works for everyone without a component change.

## What a post is

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Server-generated, unique. UUID v4 fits. |
| `author` | `{ id: number, username: string }` | The speaker. |
| `createdAt` | epoch ms | Set by the server, not the client. |
| `durationMs` | number | Client-measured; server clamps to `[MIN_VOICE_MS, MAX_VOICE_MS]` (800 ms – 60 000 ms). |
| `mimeType` | string | One of the recorder's candidates (`audio/webm;codecs=opus`, …). The server re-encodes if it must, and reports the *served* type here. |
| `peaks` | `number[56]` | 0..1 waveform preview measured on-device. Stored verbatim; the timeline never re-decodes audio to draw. |
| `parentId` | string \| null | `null` = top-level post; otherwise the id of the post this answers. One level only: a reply has no replies. |
| `likedByMe` | boolean | Per requesting user. |
| `likeCount` | number | |
| `replyCount` | number | Denormalised by the server on reply create/delete. |
| `audioUrl` | string | **Replaces the on-device `audio: Blob`.** A short-lived (≈1 h) signed URL, the same pattern `cover_image` uses. The client streams it; it never downloads the whole post to list a timeline. |

Uploads are `multipart/form-data` (audio file + the metadata fields above),
mirroring `POST /clips/`. There is no text field: a voice post is audio.

## Endpoints

| Operation | Call | Returns |
| --- | --- | --- |
| List top-level | `GET /voices/` | Newest first. Cursor-paginated like `/comments/` (`next` URL). |
| List replies | `GET /voices/?parent=<id>` | Oldest first, same page shape. |
| Create | `POST /voices/` (multipart) | The created post (201). |
| Toggle like | `POST /voices/<id>/toggle-like/` | The updated post, same envelope as `/interactions/<id>/toggle-like/`. |
| Delete | `DELETE /voices/<id>/` | 204. Deleting a post deletes its replies. Only the author may delete; everyone else gets 403. |

## Rules the client already assumes

- **The author is the requester.** The client sends no author field; the
  server derives it from the session. (The on-device store trusts the signed-in
  user the same way.)
- **A reply to a missing parent is a 404**, and the client shows "The post you
  are replying to no longer exists."
- **Validation failures are 400** with `code` in the body; the client maps
  `storage_full` → "out of space", `invalid` → the specific message. The
  server's analogue of `storage_full` is a quota policy (posts per user per
  day) and should use the same code.
- **One level of nesting.** A reply's `parentId` always names a top-level
  post; the client never renders deeper.
- **Moderation:** voice is user-generated media. The same review posture as
  cover images applies — an unreviewed post is not served to anyone but its
  author — but the policy decision (automated, human, or none) belongs to the
  owner, exactly as it did for covers.

## Swapping it in

Implement `VoiceBackend` against these endpoints (a `fetch`-based class in
`src/voices/store.ts`, next to the IndexedDB one), and change one line in
`createVoiceBackend()` to return it. `useVoices.ts` and every component stay
as they are; the test suite for the store (`src/test/voicesStore.test.ts`)
runs the same assertions against any implementation.
