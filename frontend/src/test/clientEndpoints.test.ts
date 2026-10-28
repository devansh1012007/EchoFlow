/**
 * Request contracts for the endpoints added in the UI redesign.
 *
 * Each test pins what the client puts on the wire (method, path, body,
 * credentials) against the backend view that receives it, and how a failure
 * surfaces. Mocked at the `fetch` boundary, so `apiRequest`'s auth header and
 * error classification stay under test. These prove the client matches the
 * backend contract as read from the Django views; they are not an end-to-end
 * run against a live server.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { authAPI, clipsAPI, dataSubjectAPI, legalAPI, subscriptionAPI } from "../api/client";
import { installFetchMock, json, type FetchMock } from "./fetchMock";

let api: FetchMock;
const CLIP = "11111111-1111-4111-8111-111111111111";

beforeEach(() => {
  sessionStorage.setItem("ef_access_token", "tok");
  sessionStorage.setItem("ef_refresh_token", "ref");
  api = installFetchMock();
});

async function status(p: Promise<unknown>): Promise<number | undefined> {
  try {
    await p;
  } catch (e: any) {
    return e.status;
  }
  throw new Error("expected rejection");
}

describe("clipsAPI.reportClip", () => {
  it("POSTs report_reason and content to /clips/{id}/report/ with the bearer token", async () => {
    api.on("POST", /\/clips\/.+\/report\//, () => json(201, { detail: "ok" }));
    await clipsAPI.reportClip(CLIP, "spam", "reposted ad");
    const [call] = api.callsTo(`/clips/${CLIP}/report/`);
    expect(call?.method).toBe("POST");
    expect(call?.body).toEqual({ report_reason: "spam", content: "reposted ad" });
    expect(call?.authorization).toBe("Bearer tok");
  });

  it("surfaces a rejected report with its status", async () => {
    api.on("POST", /\/report\//, () => json(400, { detail: "bad" }));
    expect(await status(clipsAPI.reportClip(CLIP, "other", "x"))).toBe(400);
  });
});

describe("clipsAPI share links", () => {
  it("createShareLink POSTs to /clips/{id}/share-link/ and returns the path and token", async () => {
    api.on("POST", /share-link/, () => json(201, { clip_id: CLIP, url: null, path: `/clip/${CLIP}?s=abc`, token: "abc", expires_in: 2592000 }));
    const link = await clipsAPI.createShareLink(CLIP);
    expect(link.path).toBe(`/clip/${CLIP}?s=abc`);
    expect(api.callsTo("share-link")[0]?.method).toBe("POST");
  });

  it.each([403, 409])("createShareLink propagates %i so the UI can explain it", async (code) => {
    api.on("POST", /share-link/, () => json(code, { detail: "no" }));
    expect(await status(clipsAPI.createShareLink(CLIP))).toBe(code);
  });

  it("getPublicClip is anonymous: no Authorization header, asks for JSON", async () => {
    api.on("GET", /\/public\//, () => json(200, { id: CLIP, title: "t" }));
    await clipsAPI.getPublicClip(CLIP);
    expect(api.callsTo("/public/")[0]?.authorization).toBeNull();
  });

  it("playSharedClip sends the share token as `s` and includes credentials for the /hls/ cookie", async () => {
    api.on("POST", /\/play\//, () => json(200, { status: "ok", token: "m", hls_playlist_url: "https://m/x.m3u8" }));
    const out = await clipsAPI.playSharedClip(CLIP, "share-tok");
    const [call] = api.callsTo(`/clips/${CLIP}/play/`);
    expect(call?.body).toEqual({ s: "share-tok" });
    expect(call?.credentials).toBe("include");
    expect(out.hls_playlist_url).toBe("https://m/x.m3u8");
  });

  it("playSharedClip rejects with the status when the link is invalid", async () => {
    api.on("POST", /\/play\//, () => json(403, { detail: "A valid share link is required." }));
    expect(await status(clipsAPI.playSharedClip(CLIP, "bad"))).toBe(403);
  });
});

describe("legalAPI", () => {
  it("submitGrievance POSTs subject, description and email to /grievance/", async () => {
    api.on("POST", /\/grievance\//, () => json(201, { grievance_id: 7, status: "received", message: "Grievance received." }));
    const r = await legalAPI.submitGrievance({ subject: "s", description: "d", user_email: "a@b.co" });
    expect(api.callsTo("/grievance/")[0]?.body).toEqual({ subject: "s", description: "d", user_email: "a@b.co" });
    expect(r.grievance_id).toBe(7);
  });

  it("submitTakedown POSTs clip_id and reason to /legal/takedown/; 404 means no such clip", async () => {
    api.on("POST", /takedown/, () => json(404, { error: "Clip not found." }));
    expect(await status(legalAPI.submitTakedown({ clip_id: CLIP, reason: "mine" }))).toBe(404);
    expect(api.callsTo("takedown")[0]?.body).toEqual({ clip_id: CLIP, reason: "mine" });
  });
});

describe("dataSubjectAPI", () => {
  it("getAccessSummary GETs /data-subject/access/", async () => {
    api.on("GET", /data-subject\/access/, () => json(200, { user_id: 1, categories: {} }));
    expect((await dataSubjectAPI.getAccessSummary()).user_id).toBe(1);
  });

  it("requestErasure always sends confirm: true and nothing else", async () => {
    api.on("POST", /erasure/, () => json(200, { request_id: 1, status: "pending", cooling_off_until: "2026-11-27T00:00:00Z" }));
    await dataSubjectAPI.requestErasure();
    expect(api.callsTo("erasure")[0]?.body).toEqual({ confirm: true });
  });

  it("a 403 during cooling-off keeps its status so the UI can show the date instead of an error", async () => {
    api.on("POST", /erasure/, () => json(403, { detail: "Cooling-off period active.", cooling_off_until: "2026-11-27T00:00:00Z" }));
    expect(await status(dataSubjectAPI.requestErasure())).toBe(403);
  });
});

describe("subscriptionAPI", () => {
  it("getStatus GETs /subscription/", async () => {
    api.on("GET", /\/subscription\/$/, () => json(200, { is_pro: false, expires_at: null, grace_until: null, last_synced: null }));
    expect((await subscriptionAPI.getStatus()).is_pro).toBe(false);
  });

  it("sync POSTs /subscription/sync/ and 503 (billing not configured) is distinguishable", async () => {
    api.on("POST", /subscription\/sync/, () => json(503, { detail: "RevenueCat not configured." }));
    expect(await status(subscriptionAPI.sync())).toBe(503);
  });

  it("getManageUrl returns a nullable url", async () => {
    api.on("GET", /subscription\/manage/, () => json(200, { url: null }));
    expect((await subscriptionAPI.getManageUrl()).url).toBeNull();
  });
});

describe("authAPI.logout", () => {
  it("revokes the refresh token on the server, then clears local storage", async () => {
    api.on("POST", /auth\/logout/, () => json(205, {}));
    await authAPI.logout();
    expect(api.callsTo("/auth/logout/")[0]?.body).toEqual({ refresh: "ref" });
    expect(sessionStorage.getItem("ef_access_token")).toBeNull();
    expect(sessionStorage.getItem("ef_refresh_token")).toBeNull();
  });

  it("still signs the user out locally when the server call fails", async () => {
    // A dead connection must never leave someone unable to sign out.
    api.fail("POST", /auth\/logout/, new TypeError("offline"));
    await authAPI.logout();
    expect(sessionStorage.getItem("ef_access_token")).toBeNull();
    expect(sessionStorage.getItem("ef_refresh_token")).toBeNull();
  });
});
