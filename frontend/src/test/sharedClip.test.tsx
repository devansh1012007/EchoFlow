import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SharedClipPage } from "../pages/SharedClip";
import { resolvePublicRoute } from "../lib/publicRoutes";
import { installFetchMock, json, type FetchMock } from "./fetchMock";

let api: FetchMock;
let play: ReturnType<typeof vi.spyOn>;
const ID = "55555555-5555-4555-8555-555555555555";
const META = { id: ID, title: "Rain on a tin roof", creator_name: "mira", duration_ms: 75000 };
const PLAYED = { status: "ok", token: "media", hls_playlist_url: "https://media.example/hls/x/master.m3u8" };

beforeEach(() => {
  api = installFetchMock();
  api.on("GET", /\/public\//, () => json(200, META));
  // jsdom does not implement media playback.
  play = vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
});
afterEach(() => play.mockRestore());

describe("resolvePublicRoute: shared clips", () => {
  it("matches /clip/<uuid> and carries the share token", () => {
    expect(resolvePublicRoute(`/clip/${ID}`, "?s=abc")).not.toBeNull();
    expect(resolvePublicRoute(`/clip/${ID}/`, "")).not.toBeNull();
  });
  it("does not capture other /clip paths", () => {
    expect(resolvePublicRoute("/clip/not-a-uuid")).toBeNull();
    expect(resolvePublicRoute(`/clips/${ID}`)).toBeNull();
  });
});

describe("SharedClipPage", () => {
  it("fetches the clip's public metadata anonymously and shows it", async () => {
    sessionStorage.setItem("ef_access_token", "should-not-be-sent");
    render(<SharedClipPage clipId={ID} shareToken="tok" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Rain on a tin roof" })).toBeInTheDocument();
    expect(screen.getByText(/@mira/)).toBeInTheDocument();
    expect(screen.getByText(/1:15/)).toBeInTheDocument();
    expect(api.callsTo("/public/")[0]?.authorization).toBeNull();
    expect(api.callsTo("/play/")).toHaveLength(0); // opening a link must not spend the exchange
  });

  it("says the clip isn't available on a 404", async () => {
    api = installFetchMock();
    api.on("GET", /\/public\//, () => json(404, { detail: "Not found." }));
    render(<SharedClipPage clipId={ID} shareToken="tok" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("This clip isn't available.");
  });

  it("on Play: exchanges the token once with credentials, then points the audio element at the stream", async () => {
    api.on("POST", /\/play\//, () => json(200, PLAYED));
    const { container } = render(<SharedClipPage clipId={ID} shareToken="tok" />);
    await userEvent.dblClick(await screen.findByRole("button", { name: "Play" }));
    const audio = container.querySelector("audio") as HTMLAudioElement;
    await vi.waitFor(() => expect(audio.getAttribute("src")).toBe(PLAYED.hls_playlist_url));
    expect(audio.crossOrigin).toBe("use-credentials"); // the cookie must travel to the media host
    const calls = api.callsTo(`/clips/${ID}/play/`);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.body).toEqual({ s: "tok" });
    expect(calls[0]?.credentials).toBe("include");
    expect(screen.queryByRole("button", { name: "Play" })).not.toBeInTheDocument();
    expect(play).toHaveBeenCalled();
  });

  it("without a share token it explains the link is incomplete and never calls /play/", async () => {
    render(<SharedClipPage clipId={ID} shareToken={null} />);
    await userEvent.click(await screen.findByRole("button", { name: "Play" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/link is incomplete/i);
    expect(api.callsTo("/play/")).toHaveLength(0);
  });

  it("an expired or forged token (403) asks for a new link and leaves Play available", async () => {
    api.on("POST", /\/play\//, () => json(403, { detail: "A valid share link is required." }));
    render(<SharedClipPage clipId={ID} shareToken="old" />);
    await userEvent.click(await screen.findByRole("button", { name: "Play" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/expired or isn't valid/);
    expect(screen.getByRole("button", { name: "Play" })).toBeEnabled();
  });

  it("explains 409 as 'still processing'", async () => {
    api.on("POST", /\/play\//, () => json(409, { detail: "Clip media is not ready." }));
    render(<SharedClipPage clipId={ID} shareToken="tok" />);
    await userEvent.click(await screen.findByRole("button", { name: "Play" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/still processing/);
  });
});
