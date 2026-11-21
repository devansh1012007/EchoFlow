import { describe, expect, it } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { StageBackdrop } from "../components/feed/StageBackdrop";
import type { FeedClip } from "../types/echoflow";

/**
 * `StageBackdrop` — the blurred surroundings of the brief. Purely decorative:
 * `aria-hidden`, no pointer events, and it removes itself when the cover URL
 * is dead rather than flashing a broken-image glyph behind the reel.
 */

function makeClip(cover: string | null): FeedClip {
  return {
    id: "clip-backdrop",
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
    cover_image: cover,
  };
}

function renderBackdrop(clip: FeedClip) {
  return render(
    <div className="relative">
      <StageBackdrop clip={clip} />
    </div>,
  );
}

describe("StageBackdrop", () => {
  it("renders nothing without a cover: the black canvas is the backdrop", () => {
    const view = renderBackdrop(makeClip(null));
    expect(view.container.querySelector("img")).toBeNull();
  });

  it("renders a blurred, decorative copy of the cover", () => {
    const view = renderBackdrop(makeClip("https://media.example/covers/clip.webp"));
    const img = view.container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("alt", "");
    // The blur is the design, stated so the next reader does not "fix" it.
    expect(img?.style.filter).toContain("blur");
    const wrap = img?.parentElement;
    expect(wrap).toHaveAttribute("aria-hidden", "true");
  });

  it("removes itself when the cover URL is dead", () => {
    const view = renderBackdrop(makeClip("https://media.example/covers/expired.webp"));
    fireEvent.error(view.container.querySelector("img")!);
    expect(view.container.querySelector("img")).toBeNull();
  });
});
