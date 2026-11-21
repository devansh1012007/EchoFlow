import { MessageSquare, Send } from "lucide-react";
import { FeedClip } from "../../types/echoflow";

interface DiscussionRailProps {
  /** The reel currently centered on the stage. */
  clip: FeedClip;
  onOpenComments: (clip: FeedClip) => void;
  onOpenShare: (clip: FeedClip) => void;
}

/**
 * The right rail: the reel's conversation, as an entry point rather than a
 * dump.
 *
 * The comments themselves load when the sheet opens (`CommentSheet` owns that
 * request), so this rail states what is known without asking — the count the
 * feed already carries — and offers the two real actions: join the
 * conversation, send the reel to someone. Both names are deliberately
 * different from the card's ("Comments, N comments" / "Share reel, N
 * shares"): the tests find the card's controls by those names, and a second
 * element with the same accessible name throws (REPO-FACTS §5).
 *
 * Zero comments is a real state with its own words, not a hidden rail: a feed
 * where every reel shows an empty conversation is a dead room, and the fix
 * for a dead room is to invite the first speaker.
 */
export function DiscussionRail({ clip, onOpenComments, onOpenShare }: DiscussionRailProps) {
  const hasComments = clip.comment_count > 0;

  return (
    <section
      aria-label="Reel discussion"
      className="w-full rounded-2xl border border-line bg-panel p-4"
    >
      <h2 className="text-[13px] font-bold tracking-wide text-fg-subtle uppercase mb-3">
        Discussion
      </h2>

      <p className="text-xs text-mist-300 leading-relaxed mb-4">
        {hasComments
          ? `${clip.comment_count} ${clip.comment_count === 1 ? "comment" : "comments"} on this reel.`
          : "No comments yet on this reel. Be the first to speak."}
      </p>

      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={() => onOpenComments(clip)}
          className="tap-target flex items-center justify-center gap-2 w-full px-4 rounded-lg bg-accent text-accent-fg text-xs font-bold hover:bg-accent-hover transition-colors"
        >
          <MessageSquare className="w-4 h-4" aria-hidden="true" />
          Join the discussion
        </button>

        <button
          type="button"
          onClick={() => onOpenShare(clip)}
          className="tap-target flex items-center justify-center gap-2 w-full px-4 rounded-lg border border-line-strong bg-canvas text-fg text-xs font-bold hover:border-accent hover:text-link transition-colors"
        >
          <Send className="w-4 h-4" aria-hidden="true" />
          Send this reel to someone
        </button>
      </div>

      <p className="mt-4 pt-3 border-t border-line text-[11px] tabular-nums text-fg-subtle">
        Shared {clip.shares} {clip.shares === 1 ? "time" : "times"}
      </p>
    </section>
  );
}
