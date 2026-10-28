import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { installFetchMock, json } from "./fetchMock";

/**
 * `navigator.language` is not always a valid BCP 47 tag. Some Linux and WebView
 * builds report a POSIX locale such as "en-US@posix", and
 * `new Intl.RelativeTimeFormat("en-US@posix")` throws a RangeError. Unguarded,
 * that took the whole Inbox down through the ErrorBoundary over a timestamp.
 *
 * The formatter is memoised at module scope, so each test loads a fresh copy of
 * the page after setting the locale.
 */

vi.mock("../stores/player", () => ({
  usePlayer: () => ({ playClip: vi.fn(), togglePlay: vi.fn(), currentClip: null, isPlaying: false }),
}));

const clip = {
  id: "clip-1", title: "Mid-Fi Crunch", creator_name: "dj", creator_id: 3, category: "music",
  hls_playlist_url: "https://media.test/x.m3u8", likes: 0, shares: 0, skips: 0, comment_count: 0,
  is_liked: false, is_following: false, duration_ms: 4000, tags: [], cover_image: null,
};

let language: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.resetModules();
  sessionStorage.setItem("ef_access_token", "tok");
  const api = installFetchMock();
  api.on("GET", /share\/inbox\//, () =>
    json(200, [{ id: 1, sender_name: "mira", clip, clip_title: "Mid-Fi Crunch", clip_hls_url: "", created_at: new Date(Date.now() - 3 * 3600_000).toISOString(), is_read: false }]),
  );
});
afterEach(() => language?.mockRestore());

describe("Inbox with an invalid navigator.language", () => {
  it.each(["en-US@posix", "not a locale", ""])("renders the list instead of crashing for %j", async (value) => {
    language = vi.spyOn(window.navigator, "language", "get").mockReturnValue(value);
    // Prove the premise: the platform really does reject this value (an empty
    // string is the one exception the spec treats as 'use the default').
    if (value !== "") expect(() => new Intl.RelativeTimeFormat(value)).toThrow(RangeError);

    const { InboxPage } = await import("../pages/Inbox");
    render(<InboxPage onRefreshUnread={() => {}} />);
    expect(await screen.findByRole("heading", { level: 2, name: /Mid-Fi Crunch/ })).toBeInTheDocument();
    // A relative time is still shown, in the runtime default locale.
    expect(screen.getByText(/3 hours ago/i)).toBeInTheDocument();
  });
});
