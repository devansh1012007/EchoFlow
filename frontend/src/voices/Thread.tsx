import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { VoiceCard } from "./VoiceCard";
import { Composer } from "./Composer";
import type { UseVoices } from "./useVoices";
import type { VoicePost } from "./types";

interface ThreadProps {
  /** The post whose replies this thread shows. */
  parent: VoicePost;
  voices: UseVoices;
  onClose: () => void;
}

/**
 * A post's replies: the parent on top (as played context, its own actions
 * disabled from here — the list is the owner of the top-level state), the
 * replies oldest first under it, and the reply composer at the foot.
 *
 * Replies are fetched when the thread opens, not when the page loads: a
 * thread nobody opens costs nothing.
 */
export function Thread({ parent, voices, onClose }: ThreadProps) {
  const [replies, setReplies] = useState<VoicePost[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped after a reply is posted, so the list re-fetches once, on purpose —
  // not on every render of the parent (whose state changes when the store
  // refreshes).
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setReplies(null);
    setError(null);
    voices
      .repliesOf(parent.id)
      .then((list) => {
        if (!cancelled) setReplies(list);
      })
      .catch(() => {
        if (!cancelled) setError("The replies could not be loaded.");
      });
    return () => {
      cancelled = true;
    };
    // `voices` is deliberately not a dependency: its identity changes with
    // every parent re-render, which would re-fetch the replies in a loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parent.id, reloadKey]);

  return (
    <section aria-label="Voice post thread" className="flex flex-col gap-4">
      <button
        type="button"
        onClick={onClose}
        className="tap-target self-start flex items-center gap-1.5 px-2 rounded-lg text-xs font-bold text-fg-muted hover:text-fg transition-colors"
      >
        <ArrowLeft className="w-4 h-4" aria-hidden="true" />
        All voice posts
      </button>

      {/* The parent, for context. Its like/delete stay live (same store,
          same ids); only its replies button is gone — this IS the replies. */}
      <VoiceCard
        post={parent}
        onToggleLike={voices.toggleLike}
        onDelete={async (id) => {
          await voices.remove(id);
          onClose();
        }}
      />

      <h2 className="text-sm font-bold tracking-wide text-fg-subtle uppercase">
        {parent.replyCount} {parent.replyCount === 1 ? "reply" : "replies"}
      </h2>

      {error && (
        <p role="alert" className="text-xs font-bold text-danger">
          {error}
        </p>
      )}

      {replies === null && !error && (
        <p role="status" className="text-xs text-fg-subtle">
          Loading replies…
        </p>
      )}

      {replies !== null && replies.length === 0 && (
        <p className="text-xs text-fg-subtle">
          No replies yet. Record the first one.
        </p>
      )}

      {replies?.map((reply) => (
        <VoiceCard
          key={reply.id}
          post={reply}
          onToggleLike={voices.toggleLike}
          onDelete={voices.remove}
        />
      ))}

      <Composer
        parentId={parent.id}
        onPost={async (input) => {
          const created = await voices.create(input);
          setReloadKey((k) => k + 1);
          return created;
        }}
      />
    </section>
  );
}
