import { FeedClip } from "../../types/echoflow";

interface InfoRailProps {
  /** The reel currently centered on the stage. */
  clip: FeedClip;
  /** 1-based position of the reel in the loaded feed, and the feed's length. */
  position: number;
  total: number;
  onOpenCreatorProfile: (creatorId: number) => void;
}

function formatDuration(ms: number): string {
  const secs = Math.round(ms / 1000);
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return `${mins}:${rem < 10 ? "0" : ""}${rem}`;
}

/**
 * The left rail: what the stage is actually playing, in the API's own words.
 *
 * Every value here is a field `FeedClipSerializer` already sends — no listener
 * counts, no "trending" lists, no similarity scores. Where the card overlays
 * its facts on the stage, the rail states them plainly beside it, which is
 * also what keeps the two from duplicating accessible names: this rail says
 * **Length**, not Duration, and its creator control is "Open profile of
 * <name>", not "@name" (REPO-FACTS §5 — the tests find the card's names with
 * `getBy...`, so a second element with the same name throws).
 *
 * Rendered only at `min-width: 1100px` (`useMediaQuery` in `Feed.tsx`, a
 * conditional render rather than a CSS hide, for exactly the same
 * name-duplication reason).
 */
export function InfoRail({ clip, position, total, onOpenCreatorProfile }: InfoRailProps) {
  return (
    <section
      aria-label="Reel information"
      className="w-full rounded-2xl border border-line bg-panel p-4"
    >
      <h2 className="text-[13px] font-bold tracking-wide text-fg-subtle uppercase mb-3">
        On the stage
      </h2>

      {/* The creator, as a control — the rail's own route to the profile.
          Deliberately NOT named "@name": the card's creator button already
          owns that name on this page. */}
      <button
        type="button"
        onClick={() => onOpenCreatorProfile(clip.creator_id)}
        className="w-full flex items-center gap-3 mb-4 group/creator text-left"
        aria-label={`Open profile of ${clip.creator_name}`}
      >
        <span
          aria-hidden="true"
          className="w-9 h-9 rounded-full border border-line-strong bg-canvas flex items-center justify-center font-bold text-sm text-link"
        >
          {clip.creator_name[0]?.toUpperCase()}
        </span>
        <span className="flex flex-col">
          <span className="text-sm font-bold text-fg group-hover/creator:text-link transition-colors">
            {clip.creator_name}
          </span>
          <span className="text-[11px] text-fg-subtle">Creator</span>
        </span>
      </button>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
        <dt className="text-fg-subtle font-bold">Length</dt>
        <dd className="text-mist-300 tabular-nums">{formatDuration(clip.duration_ms)}</dd>

        <dt className="text-fg-subtle font-bold">Category</dt>
        <dd className="text-mist-300">{clip.category}</dd>

        {clip.tags.length > 0 && (
          <>
            <dt className="text-fg-subtle font-bold">Tags</dt>
            <dd className="text-mist-300">{clip.tags.join(" · ")}</dd>
          </>
        )}

        <dt className="text-fg-subtle font-bold">Likes</dt>
        <dd className="text-mist-300 tabular-nums">{clip.likes}</dd>

        <dt className="text-fg-subtle font-bold">Comments</dt>
        <dd className="text-mist-300 tabular-nums">{clip.comment_count}</dd>

        <dt className="text-fg-subtle font-bold">Shares</dt>
        <dd className="text-mist-300 tabular-nums">{clip.shares}</dd>
      </dl>

      <p className="mt-4 pt-3 border-t border-line text-[11px] tabular-nums text-fg-subtle">
        Reel {position} of {total} in your feed
      </p>
    </section>
  );
}
