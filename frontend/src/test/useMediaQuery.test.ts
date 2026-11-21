import { describe, expect, it, vi, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useMediaQuery } from "../lib/useMediaQuery";

/**
 * `useMediaQuery` — the viewport decision that has to be a React conditional
 * (CSS-hidden elements are still in jsdom's accessibility tree, so "hidden"
 * is not "absent").
 *
 * The suite drives a controllable `matchMedia` double: `matches` is read
 * synchronously on first render, and the `change` listener the hook subscribes
 * with must actually be fired.
 */

type Listener = (event: { matches: boolean }) => void;

function installMatchMedia(initial: boolean) {
  const listeners = new Set<Listener>();
  let matches = initial;
  const mql = {
    get matches() {
      return matches;
    },
    media: "(min-width: 1100px)",
    onchange: null,
    addEventListener: (_: string, fn: Listener) => listeners.add(fn),
    removeEventListener: (_: string, fn: Listener) => listeners.delete(fn),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  };
  const original = window.matchMedia;
  window.matchMedia = vi.fn(() => mql) as unknown as typeof matchMedia;
  return {
    set(next: boolean) {
      matches = next;
      listeners.forEach((fn) => fn({ matches: next }));
    },
    restore() {
      window.matchMedia = original;
    },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useMediaQuery", () => {
  it("reads the query synchronously on first render", () => {
    const media = installMatchMedia(true);
    try {
      const { result } = renderHook(() => useMediaQuery("(min-width: 1100px)"));
      expect(result.current).toBe(true);
    } finally {
      media.restore();
    }
  });

  it("updates when the query answer changes", () => {
    const media = installMatchMedia(false);
    try {
      const { result } = renderHook(() => useMediaQuery("(min-width: 1100px)"));
      expect(result.current).toBe(false);
      act(() => media.set(true));
      expect(result.current).toBe(true);
      act(() => media.set(false));
      expect(result.current).toBe(false);
    } finally {
      media.restore();
    }
  });

  it("defaults to false when matchMedia is missing", () => {
    const original = window.matchMedia;
    // @ts-expect-error deliberately absent, as on an ancient browser
    delete window.matchMedia;
    try {
      const { result } = renderHook(() => useMediaQuery("(min-width: 1100px)"));
      expect(result.current).toBe(false);
    } finally {
      window.matchMedia = original;
    }
  });
});
