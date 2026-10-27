import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ShareLinkDialog, absoluteShareUrl } from "../components/sharing/ShareLinkDialog";
import { installFetchMock, json, type FetchMock } from "./fetchMock";

let api: FetchMock;
const ID = "44444444-4444-4444-8444-444444444444";
const LINK = { clip_id: ID, url: null, path: `/clip/${ID}?s=tok`, token: "tok", expires_in: 2592000 };

beforeEach(() => {
  sessionStorage.setItem("ef_access_token", "tok");
  api = installFetchMock();
});

describe("absoluteShareUrl", () => {
  it("prefers the server's absolute url and otherwise builds one from the current origin", () => {
    expect(absoluteShareUrl({ ...LINK, url: "https://echoflow.example/clip/x?s=t" }, "https://other")).toBe("https://echoflow.example/clip/x?s=t");
    expect(absoluteShareUrl(LINK, "https://app.example")).toBe(`https://app.example/clip/${ID}?s=tok`);
  });
});

describe("ShareLinkDialog", () => {
  it("mints exactly one link even when React runs the effect twice (StrictMode)", async () => {
    api.on("POST", /share-link/, () => json(201, LINK));
    render(
      <StrictMode>
        <ShareLinkDialog clipId={ID} clipTitle="Rain" onClose={() => {}} />
      </StrictMode>,
    );
    expect(await screen.findByLabelText("Public link")).toHaveValue(`${window.location.origin}/clip/${ID}?s=tok`);
    expect(api.callsTo("share-link")).toHaveLength(1);
  });

  it("warns that anyone with the link can listen, and for how long", async () => {
    api.on("POST", /share-link/, () => json(201, LINK));
    render(<ShareLinkDialog clipId={ID} clipTitle="Rain" onClose={() => {}} />);
    expect(await screen.findByText(/without signing in for 30 days/)).toBeInTheDocument();
  });

  it("copies the link to the clipboard and confirms", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    api.on("POST", /share-link/, () => json(201, LINK));
    render(<ShareLinkDialog clipId={ID} clipTitle="Rain" onClose={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/clip/${ID}?s=tok`);
    expect(await screen.findByText("Link copied.")).toBeInTheDocument();
  });

  it("falls back to 'press Ctrl+C' when the clipboard is unavailable", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) }, configurable: true });
    api.on("POST", /share-link/, () => json(201, LINK));
    render(<ShareLinkDialog clipId={ID} clipTitle="Rain" onClose={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Copy link" }));
    expect(await screen.findByText(/Press Ctrl\+C/)).toBeInTheDocument();
  });

  it("explains 409 as 'still processing' and shows the server's reason for a 403", async () => {
    api.on("POST", /share-link/, () => json(409, { detail: "Clip media is not ready." }));
    const { unmount } = render(<ShareLinkDialog clipId={ID} clipTitle="Rain" onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/still processing/);
    unmount();

    api = installFetchMock();
    api.on("POST", /share-link/, () => json(403, { detail: "Clip is not approved for sharing." }));
    render(<ShareLinkDialog clipId={ID} clipTitle="Rain" onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Clip is not approved for sharing.");
    expect(screen.queryByRole("button", { name: "Copy link" })).not.toBeInTheDocument();
  });
});
