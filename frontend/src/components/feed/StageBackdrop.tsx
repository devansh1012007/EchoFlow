import { useEffect, useState } from "react";
import { FeedClip } from "../../types/echoflow";

/**
 * The blurred surroundings of the brief: the reel is the subject, everything
 * behind it recedes. A heavily blurred, heavily dimmed copy of the current
 * clip's cover fills the stage behind the card; with no cover the stage is
 * simply the true-black canvas, which is the same statement at zero cost.
 *
 * Purely decorative: `aria-hidden`, no focus, no pointer events. It is not an
 * image a user can look at — it is depth, and it says nothing the card does
 * not already say in text.
 *
 * The cover URL expires (~1 h) and feed pages are cached, so a failed load is
 * expected in normal use: the backdrop unmounts itself rather than flashing a
 * broken-image glyph behind every reel.
 *
 * Performance, stated plainly: one blurred `<img>` per *stage*, not per card —
 * the feed renders one backdrop for the centered reel (the half-visible
 * neighbours get their own quiet treatment in `ReelList`). Blur is among the
 * costliest paints, which is why the chrome (`.glass`) refuses it; this single
 * element is the deliberate exception the brief asked for. If a mid-range
 * phone struggles under scroll, the fallback is `filter: none` — the black
 * stage stands on its own.
 */
export function StageBackdrop({ clip }: { clip: FeedClip }) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [clip.id, clip.cover_image]);

  if (!clip.cover_image || failed) return null;

  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <img
        src={clip.cover_image}
        alt=""
        // Scale past the frame so the blur's soft edges never show the stage
        // black through the corners.
        className="h-full w-full object-cover"
        style={{ filter: "blur(32px) saturate(0.7)", transform: "scale(1.25)" }}
        onError={() => setFailed(true)}
      />
      {/* Flat black veil — a solid colour, not a gradient: the subject stays
          the reel, and the backdrop only gives it depth. */}
      <div className="absolute inset-0" style={{ background: "var(--color-canvas)", opacity: 0.78 }} />
    </div>
  );
}
