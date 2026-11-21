import { Blob as NodeBlob } from "node:buffer";
import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it } from "vitest";
import { toPeaks } from "../voices/recorder";
import { createIndexedDbBackend, createMemoryBackend } from "../voices/store";
import { MAX_VOICE_MS, PEAK_COUNT, VoiceStoreError, type NewVoicePost, type VoiceBackend } from "../voices/types";

// jsdom's Blob does not survive structuredClone (which IndexedDB uses) or implement
// arrayBuffer(); Node's does, and real browsers have both. Real-browser Blob
// persistence is proven by scripts/recorder-harness.mjs, not here.
const blob = (bytes: Uint8Array | number[] = new Uint8Array(2048)) =>
  new NodeBlob([new Uint8Array(bytes)], { type: "audio/webm" }) as unknown as Blob;

const ME = { id: 1, username: "devansh" };
const OTHER = { id: 2, username: "asha" };

function draft(over: Partial<NewVoicePost> = {}): NewVoicePost {
  return {
    author: ME,
    durationMs: 4200,
    mimeType: "audio/webm",
    peaks: Array.from({ length: PEAK_COUNT }, (_, i) => (i % 7) / 7 + 0.1),
    parentId: null,
    audio: blob(),
    ...over,
  };
}

const backends: Array<[string, () => VoiceBackend]> = [
  ["memory", () => createMemoryBackend()],
  ["indexeddb", () => createIndexedDbBackend(new IDBFactory())],
];

describe.each(backends)("VoiceBackend (%s)", (_name, make) => {
  it("creates a post and lists it newest first", async () => {
    const b = make();
    const first = await b.create(draft());
    await new Promise((r) => setTimeout(r, 5));
    const second = await b.create(draft({ durationMs: 9000 }));
    const list = await b.list();
    expect(list.map((p) => p.id)).toEqual([second.id, first.id]);
    expect(list[0]?.audio.size).toBe(2048);
    expect(list[0]?.peaks).toHaveLength(PEAK_COUNT);
  });

  it("keeps the audio bytes round-trip intact", async () => {
    const b = make();
    const bytes = new Uint8Array(512).map((_, i) => i % 251);
    const created = await b.create(draft({ audio: blob(bytes) }));
    const got = await b.get(created.id);
    expect(new Uint8Array(await got!.audio.arrayBuffer())).toEqual(bytes);
  });

  it("replies are listed oldest first under their parent and bump replyCount", async () => {
    const b = make();
    const parent = await b.create(draft());
    const r1 = await b.create(draft({ parentId: parent.id }));
    await new Promise((r) => setTimeout(r, 5));
    const r2 = await b.create(draft({ parentId: parent.id, author: OTHER }));
    expect((await b.list({ parentId: parent.id })).map((p) => p.id)).toEqual([r1.id, r2.id]);
    expect((await b.get(parent.id))?.replyCount).toBe(2);
    expect((await b.list()).map((p) => p.id)).toEqual([parent.id]); // replies are not top-level
  });

  it("refuses a reply to a post that does not exist", async () => {
    await expect(make().create(draft({ parentId: "missing" }))).rejects.toMatchObject({ code: "not_found" });
  });

  it("toggles like on and off and never goes below zero", async () => {
    const b = make();
    const p = await b.create(draft());
    expect((await b.toggleLike(p.id)).likeCount).toBe(1);
    const off = await b.toggleLike(p.id);
    expect(off.likeCount).toBe(0);
    expect(off.likedByMe).toBe(false);
  });

  it("only the author can delete; deleting removes replies and fixes the parent's count", async () => {
    const b = make();
    const parent = await b.create(draft());
    const reply = await b.create(draft({ parentId: parent.id }));
    await expect(b.remove(parent.id, OTHER.id)).rejects.toMatchObject({ code: "forbidden" });

    await b.remove(reply.id, ME.id);
    expect((await b.get(parent.id))?.replyCount).toBe(0);

    const reply2 = await b.create(draft({ parentId: parent.id }));
    await b.remove(parent.id, ME.id);
    expect(await b.get(parent.id)).toBeNull();
    expect(await b.get(reply2.id)).toBeNull();
  });

  it("deleting something already gone is not an error", async () => {
    await expect(make().remove("nope", ME.id)).resolves.toBeUndefined();
  });

  it.each([
    ["an empty recording", { audio: blob([]) }],
    ["a too-short recording", { durationMs: 200 }],
    ["a recording far over the limit", { durationMs: MAX_VOICE_MS + 10_000 }],
    ["a missing waveform", { peaks: [] }],
  ])("rejects %s", async (_label, over) => {
    const err = await make().create(draft(over as Partial<NewVoicePost>)).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(VoiceStoreError);
    expect((err as VoiceStoreError).code).toBe("invalid");
  });

  it("clamps a duration that is only slightly over the limit instead of rejecting a legitimate auto-stop", async () => {
    const p = await make().create(draft({ durationMs: MAX_VOICE_MS + 600 }));
    expect(p.durationMs).toBe(MAX_VOICE_MS);
  });
});

describe("toPeaks", () => {
  it("returns exactly PEAK_COUNT values in 0..1 with the loudest bar at 1", () => {
    const peaks = toPeaks(Array.from({ length: 333 }, (_, i) => Math.abs(Math.sin(i / 9)) * 0.4));
    expect(peaks).toHaveLength(PEAK_COUNT);
    expect(Math.max(...peaks)).toBeCloseTo(1, 5);
    expect(Math.min(...peaks)).toBeGreaterThanOrEqual(0.08);
  });

  it("gives a flat, visible floor for silence or no samples", () => {
    expect(new Set(toPeaks([])).size).toBe(1);
    expect(Math.min(...toPeaks(new Array(100).fill(0)))).toBeGreaterThanOrEqual(0.08);
  });

  it("handles fewer samples than bars", () => {
    expect(toPeaks([0.2, 0.9, 0.4])).toHaveLength(PEAK_COUNT);
  });
});
