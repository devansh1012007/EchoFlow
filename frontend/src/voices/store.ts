import {
  MAX_VOICE_MS,
  MIN_VOICE_MS,
  PEAK_COUNT,
  VoiceStoreError,
  type NewVoicePost,
  type VoiceBackend,
  type VoicePost,
} from "./types";

/** The storage primitive both backends share. */
interface Driver {
  getAll(): Promise<VoicePost[]>;
  get(id: string): Promise<VoicePost | null>;
  put(post: VoicePost): Promise<void>;
  delete(id: string): Promise<void>;
}

function newId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `v_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function validate(input: NewVoicePost): void {
  // Duck-typed, not `instanceof Blob`: a Blob from another realm (an iframe, a
  // test runtime) is still a perfectly good recording.
  const size = (input.audio as { size?: unknown } | null | undefined)?.size;
  if (typeof size !== "number" || size === 0) {
    throw new VoiceStoreError("invalid", "There is no recording to post.");
  }
  if (input.durationMs < MIN_VOICE_MS) {
    throw new VoiceStoreError("invalid", "That recording is too short to post.");
  }
  if (input.durationMs > MAX_VOICE_MS + 1500) {
    throw new VoiceStoreError("invalid", "That recording is longer than the 60 second limit.");
  }
  if (input.peaks.length !== PEAK_COUNT) {
    throw new VoiceStoreError("invalid", "The waveform preview is missing.");
  }
}

function backendOn(driver: Driver): VoiceBackend {
  const wrap = async <T>(fn: () => Promise<T>): Promise<T> => {
    try {
      return await fn();
    } catch (error) {
      if (error instanceof VoiceStoreError) throw error;
      const name = (error as { name?: string } | null)?.name;
      if (name === "QuotaExceededError") {
        throw new VoiceStoreError("storage_full", "This device is out of space for voice posts. Delete some and try again.");
      }
      throw new VoiceStoreError("unavailable", "Voice posts can't be saved on this device right now.");
    }
  };

  return {
    list: ({ parentId = null } = {}) =>
      wrap(async () => {
        const all = await driver.getAll();
        const wanted = all.filter((p) => p.parentId === (parentId ?? null));
        return parentId ? wanted.sort((a, b) => a.createdAt - b.createdAt) : wanted.sort((a, b) => b.createdAt - a.createdAt);
      }),

    get: (id) => wrap(() => driver.get(id)),

    create: (input) =>
      wrap(async () => {
        validate(input);
        if (input.parentId) {
          const parent = await driver.get(input.parentId);
          if (!parent) throw new VoiceStoreError("not_found", "The post you are replying to no longer exists.");
          await driver.put({ ...parent, replyCount: parent.replyCount + 1 });
        }
        const post: VoicePost = {
          id: newId(),
          author: input.author,
          createdAt: Date.now(),
          durationMs: Math.min(input.durationMs, MAX_VOICE_MS),
          mimeType: input.mimeType,
          peaks: input.peaks,
          parentId: input.parentId,
          likedByMe: false,
          likeCount: 0,
          replyCount: 0,
          audio: input.audio,
        };
        await driver.put(post);
        return post;
      }),

    toggleLike: (id) =>
      wrap(async () => {
        const post = await driver.get(id);
        if (!post) throw new VoiceStoreError("not_found", "That post no longer exists.");
        const liked = !post.likedByMe;
        const next = { ...post, likedByMe: liked, likeCount: Math.max(0, post.likeCount + (liked ? 1 : -1)) };
        await driver.put(next);
        return next;
      }),

    remove: (id, requesterId) =>
      wrap(async () => {
        const post = await driver.get(id);
        if (!post) return; // already gone: deleting twice is not an error
        if (post.author.id !== requesterId) {
          throw new VoiceStoreError("forbidden", "You can only delete your own voice posts.");
        }
        const all = await driver.getAll();
        for (const reply of all.filter((p) => p.parentId === id)) await driver.delete(reply.id);
        await driver.delete(id);
        if (post.parentId) {
          const parent = await driver.get(post.parentId);
          if (parent) await driver.put({ ...parent, replyCount: Math.max(0, parent.replyCount - 1) });
        }
      }),
  };
}

export function createMemoryBackend(): VoiceBackend {
  const rows = new Map<string, VoicePost>();
  return backendOn({
    getAll: async () => [...rows.values()],
    get: async (id) => rows.get(id) ?? null,
    put: async (post) => void rows.set(post.id, post),
    delete: async (id) => void rows.delete(id),
  });
}

const DB_NAME = "echoflow-voices";
const STORE = "posts";

function openDb(factory: IDBFactory): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = factory.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("blocked"));
  });
}

function asPromise<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

/** IndexedDB keeps the audio Blobs themselves, so recordings survive a reload. */
export function createIndexedDbBackend(factory: IDBFactory = indexedDB): VoiceBackend {
  let dbPromise: Promise<IDBDatabase> | null = null;
  const db = () => (dbPromise ??= openDb(factory));
  const store = async (mode: IDBTransactionMode) => (await db()).transaction(STORE, mode).objectStore(STORE);

  return backendOn({
    getAll: async () => asPromise((await store("readonly")).getAll() as IDBRequest<VoicePost[]>),
    get: async (id) => ((await asPromise((await store("readonly")).get(id))) as VoicePost | undefined) ?? null,
    put: async (post) => void (await asPromise((await store("readwrite")).put(post))),
    delete: async (id) => void (await asPromise((await store("readwrite")).delete(id))),
  });
}

/** IndexedDB where it exists (every real browser); memory in tests and exotic runtimes. */
export function createVoiceBackend(): VoiceBackend {
  return typeof indexedDB !== "undefined" ? createIndexedDbBackend() : createMemoryBackend();
}
