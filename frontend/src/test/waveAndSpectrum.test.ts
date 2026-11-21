import { describe, expect, it, vi } from "vitest";
import { createSpectrumTap } from "../lib/spectrumTap";
import { seededBars, smoothBars } from "../lib/waveRenderer";

describe("seededBars", () => {
  it("is deterministic for the same clip id", () => {
    expect([...seededBars("clip-1", 64)]).toEqual([...seededBars("clip-1", 64)]);
  });

  it("differs between clips, so two covers do not look identical", () => {
    const a = seededBars("clip-1", 64);
    const b = seededBars("clip-2", 64);
    expect([...a]).not.toEqual([...b]);
  });

  it("stays within a visible 0.06..1 range and is quiet at both ends", () => {
    const v = seededBars("anything", 64);
    // Float32 storage: 0.06 reads back as 0.0599999986.
    expect(Math.min(...v)).toBeGreaterThanOrEqual(0.0599);
    expect(Math.max(...v)).toBeLessThanOrEqual(1);
    const mid = v.slice(24, 40).reduce((s, x) => s + x, 0) / 16;
    const edge = (v[0]! + v[63]!) / 2;
    expect(mid).toBeGreaterThan(edge);
  });
});

describe("smoothBars", () => {
  it("rises fast and falls slowly", () => {
    const state = new Float32Array(4);
    smoothBars([255, 255, 255, 255], state);
    const rose = state[0]!;
    expect(rose).toBeGreaterThan(0.5);
    smoothBars([0, 0, 0, 0], state);
    expect(state[0]!).toBeGreaterThan(rose * 0.8); // released only a little in one step
    expect(state[0]!).toBeLessThan(rose);
  });
});

describe("createSpectrumTap", () => {
  it("returns null where the browser cannot capture an element's audio (jsdom), so callers draw the fallback", () => {
    expect(createSpectrumTap(document.createElement("audio"))).toBeNull();
  });

  it("never re-routes the element: it does not call createMediaElementSource", () => {
    const createMediaElementSource = vi.fn();
    const fakeCtx = vi.fn(() => ({
      state: "running",
      createMediaElementSource,
      createAnalyser: () => ({ fftSize: 0, smoothingTimeConstant: 0, frequencyBinCount: 128, getByteFrequencyData: vi.fn() }),
      createMediaStreamSource: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
      resume: vi.fn(),
      close: vi.fn(),
    }));
    const original = window.AudioContext;
    (window as unknown as { AudioContext: unknown }).AudioContext = fakeCtx;
    try {
      const audio = document.createElement("audio") as HTMLAudioElement & { captureStream: () => MediaStream };
      audio.captureStream = () => ({ getAudioTracks: () => [{}] }) as unknown as MediaStream;
      const tap = createSpectrumTap(audio);
      expect(tap).not.toBeNull();
      audio.dispatchEvent(new Event("playing"));
      expect(createMediaElementSource).not.toHaveBeenCalled();
      tap?.dispose();
    } finally {
      (window as unknown as { AudioContext: unknown }).AudioContext = original;
    }
  });

  it("reports unavailable after dispose", () => {
    const audio = document.createElement("audio") as HTMLAudioElement & { captureStream: () => MediaStream };
    audio.captureStream = () => ({ getAudioTracks: () => [] }) as unknown as MediaStream;
    (window as unknown as { AudioContext: unknown }).AudioContext = vi.fn(() => ({ state: "running", close: vi.fn() }));
    const tap = createSpectrumTap(audio);
    tap?.dispose();
    expect(tap?.read(new Uint8Array(8))).toBe("unavailable");
  });
});
