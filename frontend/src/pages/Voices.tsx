import { useState } from "react";
import { useVoices } from "../voices/useVoices";
import { VoicePlaybackProvider } from "../voices/VoicePlayback";
import { VoiceCard } from "../voices/VoiceCard";
import { Thread } from "../voices/Thread";
import { Composer } from "../voices/Composer";
import type { VoicePost } from "../voices/types";

/**
 * Voices: speak your thoughts instead of typing them.
 *
 * The X-like timeline, except a post is a voice recording and the timeline is
 * yours alone for now: there is no backend for voices yet, so posts live on
 * this device (IndexedDB) behind a swappable interface (`VoiceBackend`), and
 * the page says so in one quiet line rather than faking other people's posts.
 * When the backend exists, this page does not change — `useVoices` does.
 */
export function VoicesPage() {
  return (
    <VoicePlaybackProvider>
      <VoicesBody />
    </VoicePlaybackProvider>
  );
}

function VoicesBody() {
  const voices = useVoices();
  const [openThread, setOpenThread] = useState<VoicePost | null>(null);

  return (
    <div className="w-full max-w-2xl mx-auto px-4 md:px-8 py-6 pb-28 flex flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl tracking-tight text-fg">Voices</h1>
        <p className="text-xs text-fg-subtle">
          Speak a thought instead of typing it. Voice posts live on this device for now — they stay
          until you delete them.
        </p>
      </header>

      <Composer onPost={voices.create} />

      {voices.error && voices.posts !== null && (
        <p role="status" aria-live="polite" className="text-xs font-bold text-danger">
          {voices.error}{" "}
          <button
            type="button"
            onClick={() => void voices.refresh()}
            className="underline font-bold"
          >
            Try again
          </button>
        </p>
      )}

      {voices.loading && voices.posts === null && (
        <p role="status" className="text-xs text-fg-subtle">
          Loading voice posts…
        </p>
      )}

      {voices.error && voices.posts === null && !voices.loading && (
        <div role="status" className="flex flex-col items-start gap-2 py-8">
          <p className="text-sm font-bold text-fg">Couldn't load your voice posts</p>
          <p className="text-xs text-fg-subtle">{voices.error}</p>
          <button
            type="button"
            onClick={() => void voices.refresh()}
            className="px-6 py-2.5 rounded bg-accent text-accent-fg text-xs font-bold hover:bg-accent-hover transition-colors"
          >
            Try again
          </button>
        </div>
      )}

      {voices.posts !== null && voices.posts.length === 0 && !voices.error && (
        <div
          role="status"
          className="flex flex-col items-center gap-2 py-12 text-center"
        >
          <p className="text-sm font-bold text-fg">No voice posts yet</p>
          <p className="text-xs text-fg-subtle max-w-xs">
            Yours would be the first on this device. Tap the button above, say what is on your
            mind, and hear it back before it posts.
          </p>
        </div>
      )}

      {openThread ? (
        <Thread
          // The fresh copy from the refreshed list, so the reply count and
          // the card state follow the store rather than the moment the
          // thread was opened.
          parent={voices.posts?.find((p) => p.id === openThread.id) ?? openThread}
          voices={voices}
          onClose={() => setOpenThread(null)}
        />
      ) : (
        <div className="flex flex-col gap-4">
          {voices.posts?.map((post) => (
            <VoiceCard
              key={post.id}
              post={post}
              onToggleLike={voices.toggleLike}
              onDelete={voices.remove}
              onOpenReplies={setOpenThread}
            />
          ))}
        </div>
      )}
    </div>
  );
}
