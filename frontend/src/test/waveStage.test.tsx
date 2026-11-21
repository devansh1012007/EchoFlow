import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WaveStage } from "../components/feed/WaveStage";
import { VizPreferenceProvider } from "../stores/viz";
import type { FeedClip } from "../types/echoflow";

/**
 * `WaveStage` — the canvas ribbon.
 *
 * jsdom has no canvas: `setupTests.ts` makes `getContext` return `null`, which
 * is exactly the degradation path the component promises for old browsers. So
 * these tests assert the contract around the drawing (element present,
 * decorative, never throwing) and drive a stub 2D context for the paths that
 * do draw.
 */

function makeClip(overrides: Partial<FeedClip> = {}): FeedClip {
  return {
    id: "clip-wave",
    title: "Night Drive",
    creator_name: "midnight_dj",
    creator_id: 3,
    category: "music",
    hls_playlist_url: null,
    likes: 0,
    shares: 0,
    skips: 0,
    comment_count: 0,
    is_liked: false,
    is_following: false,
    duration_ms: 4000,
    tags: [],
    cover_image: null,
    ...overrides,
  };
}

const playerDouble = {
  isPlaying: false,
  readSpectrum: vi.fn((out: Uint8Array) => {
    out.fill(128);
    return "live" as const;
  }),
};

vi.mock("../stores/player", () => ({
  usePlayer: () => playerDouble,
}));

/** A 2D context double: records that drawing happened, asserts nothing about pixels. */
function stubContext() {
  const ctx = {
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    quadraticCurveTo: vi.fn(),
    closePath: vi.fn(),
    fill: vi.fn(),
    stroke: vi.fn(),
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    lineJoin: "round",
    globalAlpha: 1,
  };
  const original = HTMLCanvasElement.prototype.getContext;
  // Restore the jsdom null behaviour is not needed: tests each install their own.
  HTMLCanvasElement.prototype.getContext = (() => ctx) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  return {
    ctx,
    restore() {
      HTMLCanvasElement.prototype.getContext = original;
    },
  };
}

function renderStage(clip: FeedClip, active = false) {
  return render(
    <VizPreferenceProvider>
      <div style={{ width: 300, height: 120 }}>
        <WaveStage clip={clip} active={active} className="w-full h-full" />
      </div>
    </VizPreferenceProvider>,
  );
}

describe("WaveStage", () => {
  it("renders a decorative canvas and does not throw without a 2D context", () => {
    const view = renderStage(makeClip());
    // setupTests.ts makes getContext return null: the whole point of this test.
    const canvas = view.container.querySelector("canvas");
    expect(canvas).not.toBeNull();
    expect(canvas).toHaveAttribute("aria-hidden", "true");
  });

  it("draws the idle shape when the clip is not playing", () => {
    const stub = stubContext();
    try {
      renderStage(makeClip());
      // jsdom reports clientWidth 0, so the backing store keeps the canvas
      // element's default 300x150 — the idle draw still runs, at that size.
      // What matters: the ribbon path was entered (cleared and stroked) and
      // the spectrum was NOT asked for.
      expect(stub.ctx.clearRect).toHaveBeenCalledWith(0, 0, 300, 150);
      expect(stub.ctx.stroke).toHaveBeenCalled();
    } finally {
      stub.restore();
    }
  });

  it("never asks for a spectrum while idle", () => {
    renderStage(makeClip(), false);
    expect(playerDouble.readSpectrum).not.toHaveBeenCalled();
  });
});
