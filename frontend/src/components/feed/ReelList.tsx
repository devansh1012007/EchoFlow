import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { FeedClip } from "../../types/echoflow";
import { usePlayer } from "../../stores/player";
import { ReelCard } from "./ReelCard";
import { StageBackdrop } from "./StageBackdrop";

interface Props {
  clips: FeedClip[];
  loading: boolean;
  hasMore: boolean;
  /**
   * `llen(user_feed:{user_id})`, read by the server AFTER the destructive
   * `lpop` (`views/feed.py:103`) — how many clips are still queued for this
   * user. `null` when the server did not say (a 202 cold response, or the
   * degraded fallback where the Redis read itself failed).
   */
  queueHealth?: number | null;
  isLoadingMore?: boolean;
  onLoadMore?: () => void;
  onOpenCreatorProfile: (creatorId: number) => void;
  onOpenComments: (clip: FeedClip) => void;
  onOpenShare: (clip: FeedClip) => void;
  /**
   * The centered reel changed — the reel the stage is currently about. The
   * feed's rails and the stage backdrop follow this, because "which reel is
   * the subject" is a stage-level fact, not a card-level one.
   */
  onCenteredChange?: (index: number, clip: FeedClip) => void;
  /**
   * Ask the stage to bring a reel to the center (and focus it), by index.
   * Changes are acted on; `null` is ignored. Used by the feed to land on a
   * reel from outside the scroller without tearing down scroll state.
   */
  scrollToIndex?: number | null;
}

/** The stage's height: viewport minus the header + bottom-nav allowance. */
const STAGE_HEIGHT = "calc(100vh - 156px)";
/**
 * How much of the stage one reel takes. The reel either side of the centered
 * one stays half-visible above and below — the brief's "show the reel above
 * and below the main reel" — so the feed reads as a strip you move through,
 * not as one card at a time with nothing beyond it.
 */
const REEL_SHARE = 0.78;
const REEL_HEIGHT = `calc(${STAGE_HEIGHT} * ${REEL_SHARE})`;
/** Half of the remaining stage, as a spacer so the first and last reels can center. */
const SPACER_HEIGHT = `calc(${STAGE_HEIGHT} * ${(1 - REEL_SHARE) / 2})`;
/** Ported from sample_frontend2. Comfortably above the 0.5 threshold that makes
 *  a reel flip mid-watch on a phone with a system scrollbar. */
const AUTOPLAY_THRESHOLD = 0.6;
/** Pacing: hold a beat after the clip finishes before advancing, so
 *  auto-advance does not feel like a skip. */
const ADVANCE_DELAY_MS = 1000;

/**
 * `scroll-behavior` is `smooth` globally (`tokens.css:163`) and the two calls
 * below ask for it explicitly, which is 2.2.2 "Animation from Interactions"
 * (WCAG A) failing for anyone who has asked their OS to stop moving things.
 *
 * Read once per call rather than subscribing, so a mid-session change to the
 * OS setting is picked up without a listener.
 */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * The feed stage: a vertical scroll-snap column with the centered reel as the
 * subject and the neighbouring reels half-visible, receded, around it.
 *
 * Viewability autoplay is driven by an `IntersectionObserver` on the item
 * wrappers (ported from sample_frontend2, preserved in git as 20451d3). The
 * observer is ALWAYS installed so `onCenteredChange` fires in manual mode
 * too; the `playClip` inside it is what `handsFreeMode` gates.
 */
export function ReelList({
  clips,
  loading,
  hasMore,
  queueHealth = null,
  isLoadingMore = false,
  onLoadMore,
  onOpenCreatorProfile,
  onOpenComments,
  onOpenShare,
  onCenteredChange,
  scrollToIndex,
}: Props) {
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const itemRefs = useRef<(HTMLDivElement | null)[]>([]);
  const hintId = useId();
  const { currentClip, progress, playClip, handsFreeMode } = usePlayer();
  const currentClipRef = useRef<string | undefined>(currentClip?.id);
  useEffect(() => {
    currentClipRef.current = currentClip?.id;
  }, [currentClip?.id]);

  /**
   * The reel the stage is centered on, by index. Drives the neighbours'
   * receded treatment and (through `onCenteredChange`) the feed's rails and
   * the backdrop behind the stage.
   */
  const [centeredIndex, setCenteredIndex] = useState<number>(0);
  const centeredRef = useRef<number>(-1);

  /** Report a new centered reel once, to state and to the caller. */
  const reportCentered = useCallback(
    (index: number) => {
      if (centeredRef.current === index) return;
      centeredRef.current = index;
      setCenteredIndex(index);
      const clip = clips[index];
      if (clip) onCenteredChange?.(index, clip);
    },
    [clips, onCenteredChange],
  );
  const reportCenteredRef = useRef(reportCentered);
  useEffect(() => {
    reportCenteredRef.current = reportCentered;
  }, [reportCentered]);

  /**
   * What the feed just did, in words.
   *
   * `scrollIntoView` moves the viewport and nothing else, and each card is a
   * fresh subtree across clips (`key={clip.id}` below), so a keyboard user who
   * tabbed to a control inside the card that just finished had that focus
   * destroyed and dropped to `<body>` — mid-card, with no announcement and no
   * way to tell which reel had started. One polite live region, matching the
   * `role="status" aria-live="polite"` idiom `ReelCard` already uses for its
   * like/follow failures.
   */
  const [announcement, setAnnouncement] = useState("");

  /**
   * Move to a reel, deliberately.
   *
   * Focus follows the viewport because a `role="group"` + `tabIndex={-1}`
   * wrapper can receive focus without joining the tab order, and the
   * announcement says which reel arrived so the change is not silent. Held in a
   * ref so the auto-advance effect below can depend on `clips` without
   * restarting its one-second delay on every render.
   */
  const focusReel = useCallback(
    (index: number, reason: "auto-advance" | "keyboard") => {
      const el = itemRefs.current[index];
      const clip = clips[index];
      if (!el || !clip) return;
      el.scrollIntoView({
        behavior: prefersReducedMotion() ? "auto" : "smooth",
        block: "center",
      });
      el.focus({ preventScroll: true });
      reportCentered(index);
      setAnnouncement(
        reason === "auto-advance"
          ? `Next reel, ${index + 1} of ${clips.length}: ${clip.title}`
          : `Reel ${index + 1} of ${clips.length}: ${clip.title}`,
      );
    },
    [clips, reportCentered],
  );
  const focusReelRef = useRef(focusReel);
  useEffect(() => {
    focusReelRef.current = focusReel;
  }, [focusReel]);

  // Bring an externally named reel to the center. The feed names an index; the
  // stage owns the scroll. `scrollToIndex` changing identity is the signal.
  useEffect(() => {
    if (scrollToIndex == null || scrollToIndex < 0 || scrollToIndex >= clips.length) return;
    focusReelRef.current(scrollToIndex, "keyboard");
  }, [scrollToIndex, clips.length]);

  // Pagination sentinel — separate observer from autoplay so that fetching the
  // next page can never start a clip.
  useEffect(() => {
    if (!sentinelRef.current || !onLoadMore) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (!entry) return;
        if (entry.isIntersecting && hasMore && !loading) onLoadMore();
      },
      { threshold: 0.1 },
    );
    observer.observe(sentinelRef.current);
    return () => observer.disconnect();
  }, [clips.length, hasMore, loading, onLoadMore]);

  // The most-visible reel: always reported (the rails follow it), and played
  // when hands-free mode is on. `handsFreeMode` off means nothing plays until
  // the user asks for it — the observer stays live because the stage still
  // needs to know where it is.
  useEffect(() => {
    if (!clips.length) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const index = Number((entry.target as HTMLElement).dataset.index);
          const clip = clips[index];
          reportCenteredRef.current(index);
          // Guard on id, not on isPlaying: several entries can intersect in one
          // callback, and each playClip would restart the source.
          if (handsFreeMode && clip && currentClipRef.current !== clip.id) {
            playClip(clip, clips);
          }
        });
      },
      { threshold: AUTOPLAY_THRESHOLD },
    );
    itemRefs.current.forEach((ref) => ref && observer.observe(ref));
    return () => observer.disconnect();
  }, [clips, playClip, handsFreeMode]);

  // Auto-advance on completion. The one and only advance trigger on this path.
  useEffect(() => {
    if (!handsFreeMode) return;
    if (!currentClip || progress < 0.99) return;
    const index = clips.findIndex((c) => c.id === currentClip.id);
    if (index < 0 || index >= clips.length - 1) return;
    const timer = setTimeout(
      () => focusReelRef.current(index + 1, "auto-advance"),
      ADVANCE_DELAY_MS,
    );
    return () => clearTimeout(timer);
  }, [currentClip, progress, clips, handsFreeMode]);

  /**
   * Reel-to-reel keyboard navigation.
   *
   * The scroll container is the keyboard surface: arrow keys, PageUp/PageDown,
   * Home and End move between reels, with the wrapper receiving focus so the
   * reel's own controls stay one Tab away.
   */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const last = clips.length - 1;
    if (last < 0) return;
    const focused = itemRefs.current.findIndex((el) => el === document.activeElement);
    const from = focused < 0 ? centeredRef.current < 0 ? 0 : centeredRef.current : focused;
    let target: number;
    switch (event.key) {
      case "ArrowDown":
      case "PageDown":
        target = Math.min(last, from + 1);
        break;
      case "ArrowUp":
      case "PageUp":
        target = Math.max(0, from - 1);
        break;
      case "Home":
        target = 0;
        break;
      case "End":
        target = last;
        break;
      default:
        return;
    }
    event.preventDefault();
    focusReel(target, "keyboard");
  };

  if (!clips.length) {
    return <ListEmpty hasMore={hasMore} isLoadingMore={isLoadingMore} onLoadMore={onLoadMore} />;
  }

  const endOfFeed = (
    <div
      className="flex flex-col items-center gap-3 text-center"
      style={{ flex: "0 0 100%", padding: 24 }}
    >
      {hasMore && onLoadMore ? (
        <>
          <button
            type="button"
            onClick={onLoadMore}
            disabled={isLoadingMore}
            className="px-6 py-2.5 rounded-xl border border-line bg-panel text-fg text-xs font-bold hover:border-accent hover:text-link transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {isLoadingMore ? "Loading..." : "Load more reels"}
          </button>
          {queueHealth !== null && queueHealth > 0 && (
            <p className="text-[11px] tabular-nums text-fg-subtle">
              {queueHealth} more reel{queueHealth === 1 ? "" : "s"} queued for you
            </p>
          )}
        </>
      ) : (
        // Reached only when the server says this is the end: a response with
        // no `next`, or `queue_health` of 0 on a non-degraded page.
        <p
          role="status"
          aria-live="polite"
          className="text-center text-fg-subtle"
          style={{ fontSize: 11, letterSpacing: "0.08em" }}
        >
          All caught up
        </p>
      )}
    </div>
  );

  const centeredClip = clips[centeredIndex] ?? null;

  return (
    <>
      <div className="relative mx-auto w-full" style={{ height: STAGE_HEIGHT, maxWidth: "var(--content-max)" }}>
        {/* The blurred surroundings: a dimmed copy of the centered reel's
            cover behind the strip, so the subject sits in depth. No cover, no
            backdrop — the true black is the statement. */}
        {centeredClip && <StageBackdrop clip={centeredClip} />}

        <div
          role="region"
          aria-label="Reel feed"
          aria-describedby={hintId}
          tabIndex={0}
          onKeyDown={onKeyDown}
          className="scrollbar-hide relative flex flex-col overflow-y-auto mx-auto w-full h-full"
          style={{ scrollSnapType: "y mandatory" }}
        >
          {/* Spacers so the first and last reels can reach the center. */}
          <div aria-hidden="true" style={{ flex: `0 0 ${SPACER_HEIGHT}` }} />

          {clips.map((clip, i) => (
            <div
              key={clip.id}
              data-index={i}
              tabIndex={-1}
              role="group"
              aria-label={`Reel ${i + 1} of ${clips.length}: ${clip.title}`}
              ref={(el) => {
                itemRefs.current[i] = el;
                // Arm the pagination sentinel one item from the end.
                if (i === clips.length - 2) sentinelRef.current = el;
              }}
              // The neighbours recede: blurred and dimmed, present enough to
              // say "there is more above and below", never competing with the
              // centered reel. CSS in tokens.css; the transition is disabled
              // under prefers-reduced-motion by the global block.
              data-active={i === centeredIndex}
              className="reel-item flex items-center justify-center relative"
              style={{
                flex: `0 0 ${REEL_HEIGHT}`,
                minHeight: REEL_HEIGHT,
                scrollSnapAlign: "center",
                padding: "0 4px",
              }}
            >
              <div className="w-full h-full">
                <ReelCard
                  clip={clip}
                  isActive={currentClip?.id === clip.id}
                  onOpenComments={onOpenComments}
                  onOpenShare={onOpenShare}
                  onCreatorClick={onOpenCreatorProfile}
                />
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex items-center justify-center" style={{ flex: `0 0 ${REEL_HEIGHT}` }}>
              <div className="skeleton h-1 w-3/5" />
            </div>
          )}

          {endOfFeed}

          <div aria-hidden="true" style={{ flex: `0 0 ${SPACER_HEIGHT}` }} />
        </div>
      </div>

      <p id={hintId} className="sr-only">
        Reel feed. Use the up and down arrow keys, Page Up and Page Down, or Home and End to move
        between reels. Reels play automatically while hands-free mode is on.
      </p>
      <span role="status" aria-live="polite" className="sr-only">
        {announcement}
      </span>
    </>
  );
}

/**
 * A genuinely empty feed, and the one state that says so.
 *
 * Distinct from a cold queue, which the caller owns: five exhausted retries
 * render an explicit "still preparing" screen, because telling a recommender's
 * user that there is nothing here when the queue is merely cold is the single
 * most damaging wrong conclusion this page can show. `role="status"` copies
 * the idiom at `ReelCard.tsx:302` — the states below announce nothing at all
 * before this.
 */
function ListEmpty({
  hasMore,
  isLoadingMore,
  onLoadMore,
}: {
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore?: () => void;
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col items-center gap-4 text-center"
      style={{ padding: "64px 24px" }}
    >
      <div
        className="flex items-center justify-center"
        style={{
          width: 64,
          height: 64,
          borderRadius: "var(--radius-full)",
          background: "var(--surface-container)",
        }}
      >
        <svg
          width="28"
          height="28"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--text-tertiary)"
          strokeWidth="2"
        >
          <path d="M12 1a3 3 0 0 1 3 3v8a3 3 0 0 1-6 0V4a3 3 0 0 1 3-3z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v2" />
        </svg>
      </div>
      <p
        style={{ fontFamily: "var(--font-display)", fontSize: 18, fontWeight: 700 }}
      >
        Nothing here yet
      </p>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", maxWidth: 220, lineHeight: 1.5 }}>
        Check back soon for fresh audio reels
      </p>
      {hasMore && onLoadMore && (
        <button
          type="button"
          onClick={onLoadMore}
          disabled={isLoadingMore}
          className="px-6 py-2.5 rounded-xl border border-line bg-panel text-fg text-xs font-bold hover:border-accent hover:text-link transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {isLoadingMore ? "Loading..." : "Load more reels"}
        </button>
      )}
    </div>
  );
}
