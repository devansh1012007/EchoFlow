import { useCallback, useEffect, useRef } from "react";
import { FeedClip } from "../../types/echoflow";
import { usePlayer } from "../../stores/player";
import { useVizPreference } from "../../stores/viz";
import { drawWave, seededBars, smoothBars } from "../../lib/waveRenderer";

interface WaveStageProps {
  /** The clip whose energy is drawn. Its id seeds the static shape. */
  clip: FeedClip;
  /** True only on the card the player considers current. */
  active: boolean;
  /** CSS classes for the canvas' wrapper. The canvas fills it. */
  className?: string;
}

/** How many points the ribbon is drawn with. 64 reads as smooth, not blocky. */
const POINTS = 64;

/** Palette for `drawWave`, resolved from the tokens the page already loaded. */
function palette(canvas: HTMLCanvasElement): { accent: string; axis: string } {
  const style = getComputedStyle(canvas);
  return {
    accent: style.getPropertyValue("--color-accent").trim() || "#3dffb5",
    axis: style.getPropertyValue("--color-fg-subtle").trim() || "#8e8e8e",
  };
}

/**
 * The reel's sound wave, on a canvas.
 *
 * Three states, one component:
 *
 * - **Live** — the active card while audio plays: each frame reads real
 *   magnitudes from the player's spectrum tap (`readSpectrum`) and draws them
 *   through `waveRenderer` with fast-attack/slow-release smoothing.
 * - **Idle shape** — every other card, and the active card while paused: the
 *   deterministic `seededBars` shape for this clip. It is a *cover*, not a
 *   recording: nothing pretends to be measured audio. Same shape every render,
 *   because it is seeded by the clip id.
 * - **Viz off** — the person turned the wave off: the seeded shape is drawn
 *   once and no animation loop runs at all (also the `--reduced-motion`
 *   default, which resolves the cookie to "off" before React mounts).
 *
 * The canvas is `aria-hidden` and carries no text alternative: the wave is
 * ornament around the clip's real information (title, creator, length), and an
 * `aria-label` here would be a description of decoration, announced on every
 * card in the feed.
 *
 * Never throws when the 2D context is unavailable (old browsers, jsdom): the
 * element renders and nothing is drawn, which degrades to the black stage the
 * card already provides.
 */
export function WaveStage({ clip, active, className }: WaveStageProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const { isPlaying, readSpectrum } = usePlayer();
  const { vizOn } = useVizPreference();

  const live = active && isPlaying && vizOn;

  /** Size the backing store to the element (x DPR). Returns the 2D context, or null. */
  const prepare = useCallback((): CanvasRenderingContext2D | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    const dpr = window.devicePixelRatio || 1;
    const width = Math.round(canvas.clientWidth * dpr);
    const height = Math.round(canvas.clientHeight * dpr);
    if (width >= 2 && height >= 2 && (canvas.width !== width || canvas.height !== height)) {
      canvas.width = width;
      canvas.height = height;
    }
    return ctx;
  }, []);

  // The idle / viz-off draw: once per clip and on every resize while idle.
  // `live` is a dependency so pausing swaps the ribbon back to the clip's own
  // shape instead of freezing the last measured frame mid-word.
  useEffect(() => {
    if (live) return;
    const canvas = canvasRef.current;
    if (!canvas || typeof ResizeObserver === "undefined") return;
    const draw = () => {
      const ctx = prepare();
      if (!ctx) return;
      drawWave(ctx, canvas.width, canvas.height, seededBars(clip.id, POINTS), palette(canvas));
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [live, clip.id, prepare]);

  // The live loop. One rAF chain while `live`, none otherwise.
  useEffect(() => {
    if (!live) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    const raw = new Uint8Array(POINTS);
    const state = new Float32Array(POINTS);
    const seeded = seededBars(clip.id, POINTS);
    let frame = 0;

    const draw = () => {
      frame = requestAnimationFrame(draw);
      const ctx = prepare();
      if (!ctx) return;
      const { width, height } = canvas;
      if (width < 2 || height < 2) return;
      const colors = palette(canvas);
      const verdict = readSpectrum(raw);
      if (verdict === "live" || verdict === "silent") {
        drawWave(ctx, width, height, smoothBars(raw, state), colors);
      } else {
        // No data path (no captureStream, or the stream gave up): the seeded
        // shape. Deterministic, so it is stable frame to frame.
        drawWave(ctx, width, height, seeded, colors);
      }
    };

    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [live, clip.id, prepare, readSpectrum]);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      aria-hidden="true"
      // A canvas with no 2D context (or no size yet) shows as a transparent
      // hole; the stage's own black is what should show through.
      style={{ display: "block", width: "100%", height: "100%" }}
    />
  );
}
