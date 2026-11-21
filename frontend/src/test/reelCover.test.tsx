import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { ReelCover } from "../components/feed/ReelCover";
import { VizPreferenceProvider } from "../stores/viz";
import type { FeedClip } from "../types/echoflow";

/**
 * `ReelCover` — the clip's imagery: the creator's cover when there is one, the
 * sound wave when there is not.
 *
 * The load-bearing case is the expired presigned URL: feed pages are cached in
 * `sessionStorage` and the cover link dies after about an hour, so an `<img>`
 * that errors must fall back to the wave instead of showing a broken-image
 * glyph. The cover is also `null` until a reviewer approves it, which is the
 * common case, not the exception.
 */

function makeClip(overrides: Partial<FeedClip> = {}): FeedClip {
  return {
    id: "clip-cover",
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
    out.fill(0);
    return "unavailable" as const;
  }),
};

vi.mock("../stores/player", () => ({
  usePlayer: () => playerDouble,
}));

function renderCover(clip: FeedClip, active = false) {
  return render(
    <VizPreferenceProvider>
      <ReelCover clip={clip} active={active} />
    </VizPreferenceProvider>,
  );
}

describe("ReelCover", () => {
  it("renders the wave when the clip has no cover", () => {
    const view = renderCover(makeClip({ cover_image: null }));
    expect(view.container.querySelector("canvas")).not.toBeNull();
    expect(view.container.querySelector("img")).toBeNull();
  });

  it("renders the cover image when there is one", () => {
    const view = renderCover(
      makeClip({ cover_image: "https://media.example/covers/clip-cover.webp" }),
    );
    const img = view.container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "https://media.example/covers/clip-cover.webp");
    // Decorative: the title and creator are real text beside the stage.
    expect(img).toHaveAttribute("alt", "");
    expect(view.container.querySelector("canvas")).toBeNull();
  });

  it("falls back to the wave when the cover URL is dead", () => {
    const view = renderCover(
      makeClip({ cover_image: "https://media.example/covers/expired.webp" }),
    );
    const img = view.container.querySelector("img");
    expect(img).not.toBeNull();
    fireEvent.error(img!);
    // The expired-URL case: an <img> that errors must not stay on screen.
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.querySelector("canvas")).not.toBeNull();
  });

  it("gives a new clip's cover its own chance after a failure", () => {
    const view = renderCover(
      makeClip({ id: "one", cover_image: "https://media.example/covers/dead.webp" }),
    );
    fireEvent.error(view.container.querySelector("img")!);
    expect(view.container.querySelector("canvas")).not.toBeNull();

    view.rerender(
      <VizPreferenceProvider>
        <ReelCover
          clip={makeClip({ id: "two", cover_image: "https://media.example/covers/live.webp" })}
          active={false}
        />
      </VizPreferenceProvider>,
    );
    const img = view.container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "https://media.example/covers/live.webp");
  });
});
