/**
 * Voices: short spoken posts, the audio-first answer to a text timeline.
 *
 * There is NO backend for this yet. `VoiceBackend` is the seam: today it is
 * implemented on-device (store.ts); a server implementation replaces
 * `createVoiceBackend()` later without touching any component. The planned
 * HTTP contract is written down in frontend/VOICES_BACKEND.md.
 */
export const MAX_VOICE_MS = 60_000;
export const MIN_VOICE_MS = 800;
/** Bars in a stored waveform preview. */
export const PEAK_COUNT = 56;

export interface VoiceAuthor {
  id: number;
  username: string;
}

export interface VoicePost {
  id: string;
  author: VoiceAuthor;
  /** Epoch milliseconds. */
  createdAt: number;
  durationMs: number;
  mimeType: string;
  /** PEAK_COUNT values in 0..1, measured from the microphone while recording. */
  peaks: number[];
  /** null = a top-level post; otherwise the id of the post this answers. */
  parentId: string | null;
  likedByMe: boolean;
  likeCount: number;
  replyCount: number;
  /** On-device mode only. A server backend would carry `audioUrl` instead. */
  audio: Blob;
}

export interface NewVoicePost {
  author: VoiceAuthor;
  durationMs: number;
  mimeType: string;
  peaks: number[];
  parentId: string | null;
  audio: Blob;
}

export type VoiceStoreErrorCode = "unavailable" | "storage_full" | "not_found" | "forbidden" | "invalid";

export class VoiceStoreError extends Error {
  constructor(public readonly code: VoiceStoreErrorCode, message: string) {
    super(message);
    this.name = "VoiceStoreError";
  }
}

export interface VoiceBackend {
  /** parentId undefined/null: top-level posts, newest first. A string: replies, oldest first. */
  list(opts?: { parentId?: string | null }): Promise<VoicePost[]>;
  get(id: string): Promise<VoicePost | null>;
  create(input: NewVoicePost): Promise<VoicePost>;
  toggleLike(id: string): Promise<VoicePost>;
  /** Only the author may delete. Deleting a post deletes its replies. */
  remove(id: string, requesterId: number): Promise<void>;
}
