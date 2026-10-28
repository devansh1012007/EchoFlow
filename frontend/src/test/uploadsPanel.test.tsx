import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UploadsPanel, MAX_UPLOAD_PAGES, statusLabel } from "../components/profile/UploadsPanel";
import { clipsAPI } from "../api/client";
import { installFetchMock, json, type FetchMock } from "./fetchMock";

let api: FetchMock;
const row = (n: number, status: string) => ({ id: `id-${n}`, title: `Clip ${n}`, category: "music", status });
const page = (results: ReturnType<typeof row>[], next: string | null = null) => json(200, { count: results.length, next, previous: null, results });

beforeEach(() => {
  // The client attaches a bearer token only when a refresh token is stored as well.
  sessionStorage.setItem("ef_access_token", "tok");
  sessionStorage.setItem("ef_refresh_token", "ref");
  api = installFetchMock();
});

describe("clipsAPI.listMyClips", () => {
  it("GETs /clips/?page=N with the bearer token", async () => {
    api.on("GET", /\/clips\/\?page=3/, () => page([]));
    await clipsAPI.listMyClips(3);
    const [c] = api.callsTo("/clips/?page=3");
    expect(c?.method).toBe("GET");
    expect(c?.authorization).toBe("Bearer tok");
  });
});

describe("statusLabel", () => {
  it("names the known statuses and degrades to a readable form for unknown ones", () => {
    expect(statusLabel("processing")).toBe("Processing");
    expect(statusLabel("failed")).toBe("Failed");
    expect(statusLabel("rejected")).toBe("Rejected");
    expect(statusLabel("quarantined")).toBe("Quarantined");
  });
});

describe("UploadsPanel", () => {
  it("lists clips that are not ready and leaves published ones out", async () => {
    api.on("GET", /\/clips\/\?page=1/, () => page([row(1, "ready"), row(2, "processing"), row(3, "failed")]));
    render(<UploadsPanel />);
    expect(await screen.findByText("Clip 2")).toBeInTheDocument();
    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(screen.getByText("Clip 3")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.queryByText("Clip 1")).not.toBeInTheDocument();
  });

  it("says plainly when nothing is in progress", async () => {
    api.on("GET", /\/clips\/\?page=1/, () => page([row(1, "ready")]));
    render(<UploadsPanel />);
    expect(await screen.findByText(/Nothing is processing/)).toBeInTheDocument();
  });

  it("follows `next` across pages, so a stuck clip on page 2 is still found", async () => {
    api.on("GET", /\/clips\/\?page=1/, () => page([row(1, "ready")], "http://x/clips/?page=2"));
    api.on("GET", /\/clips\/\?page=2/, () => page([row(2, "processing")]));
    render(<UploadsPanel />);
    expect(await screen.findByText("Clip 2")).toBeInTheDocument();
    expect(api.callsTo("/clips/?page=")).toHaveLength(2);
  });

  it("stops at the page cap and says it did not check everything", async () => {
    api.on("GET", /\/clips\/\?page=\d+/, () => page([row(1, "ready")], "http://x/more"));
    render(<UploadsPanel />);
    expect(await screen.findByText(/Only your first 100 uploads were checked/)).toBeInTheDocument();
    expect(api.callsTo("/clips/?page=")).toHaveLength(MAX_UPLOAD_PAGES);
  });

  it("shows a retryable status on failure without raising a page alert", async () => {
    api.fail("GET", /\/clips\/\?page=1/, new TypeError("offline"));
    render(<UploadsPanel />);
    expect(await screen.findByText(/Can't reach EchoFlow/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    api = installFetchMock(); // first matching handler wins, so start clean for the retry
    api.on("GET", /\/clips\/\?page=1/, () => page([row(9, "processing")]));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Clip 9")).toBeInTheDocument();
  });
});
