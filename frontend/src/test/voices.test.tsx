import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Blob as NodeBlob } from "node:buffer";
import { VoicesPage } from "../pages/Voices";
import { createMemoryBackend } from "../voices/store";
import { PEAK_COUNT, type NewVoicePost, type VoiceBackend, type VoicePost } from "../voices/types";

/**
 * The Voices page: speak a post instead of typing it.
 *
 * The store itself is `voicesStore.test.ts`'s subject (both backends); this
 * file is the page over the real memory backend — seeded directly, so the
 * components are exercised against exactly the contract `useVoices` promises.
 * The recorder is doubled (jsdom has no microphone), the player store is
 * doubled (its own harness lives in `playerStore.test.tsx`), and the
 * `VoicePlayback` coordination — one voice at a time, the feed always wins —
 * is asserted through what the buttons say.
 */

const ME = { id: 1, username: "devansh" };

vi.mock("../stores/auth", () => ({
  useAuth: () => ({ user: ME, isAuthenticated: true }),
}));

/** The feed-player double. `currentClip` is mutated by a test, then the page is re-rendered. */
const playerState = vi.hoisted(() => ({
  currentClip: null as { id: string } | null,
  isPlaying: false,
  pause: vi.fn(),
}));

vi.mock("../stores/player", () => ({
  usePlayer: () => playerState,
}));

/** One shared real backend, so a test can seed posts before the page reads them. */
const backendRef = vi.hoisted(() => ({ current: null as VoiceBackend | null }));

vi.mock("../voices/store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../voices/store")>();
  return {
    ...actual,
    createVoiceBackend: () => backendRef.current ?? (backendRef.current = actual.createMemoryBackend()),
  };
});

/** A controllable recorder double: start/stop are driven by the test. */
const recorderState = vi.hoisted(() => ({
  failStartWith: null as { code: string; message: string } | null,
  lastOpts: null as {
    onLevel?: (level: number, ms: number) => void;
    onAutoStop?: (r: unknown) => void;
  } | null,
}));

vi.mock("../voices/recorder", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../voices/recorder")>();
  class MockRecorder {
    constructor(opts: Record<string, unknown>) {
      recorderState.lastOpts = opts as typeof recorderState.lastOpts;
    }
    async start(): Promise<void> {
      if (recorderState.failStartWith) {
        throw new actual.VoiceRecorderError(
          recorderState.failStartWith.code as "denied",
          recorderState.failStartWith.message,
        );
      }
    }
    async stop() {
      return {
        blob: new NodeBlob([new Uint8Array(512)], { type: "audio/webm" }) as unknown as Blob,
        mimeType: "audio/webm",
        durationMs: 3200,
        peaks: Array.from({ length: PEAK_COUNT }, (_, i) => 0.3 + (i % 5) / 10),
      };
    }
    cancel(): void {}
  }
  return { ...actual, VoiceRecorder: MockRecorder };
});

const blob = () => new NodeBlob([new Uint8Array(1024)], { type: "audio/webm" }) as unknown as Blob;

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

function renderPage() {
  const view = render(<VoicesPage />);
  return {
    view,
    rerender: () => view.rerender(<VoicesPage />),
  };
}

beforeEach(() => {
  backendRef.current = createMemoryBackend();
  playerState.currentClip = null;
  playerState.isPlaying = false;
  playerState.pause.mockClear();
  recorderState.failStartWith = null;
  recorderState.lastOpts = null;
});

describe("Voices — the honest single-device note", () => {
  it("says where posts live, on the page, in one line", async () => {
    renderPage();
    expect(
      await screen.findByText(/voice posts live on this device for now/i),
    ).toBeInTheDocument();
  });

  it("shows an empty state that invites the first post", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/no voice posts yet/i)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /record a voice post/i })).toBeInTheDocument();
  });
});

describe("Voices — recording and posting", () => {
  it("walks record → review → post and shows the new post", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText(/no voice posts yet/i)).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /record a voice post/i }));
    expect(screen.getByRole("button", { name: /stop recording/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /stop recording/i }));
    expect(screen.getByRole("button", { name: /play back recording/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^post voice$/i }));
    const card = await screen.findByRole("article");
    expect(card).toHaveTextContent("devansh");
    expect(screen.queryByText(/no voice posts yet/i)).not.toBeInTheDocument();
  });

  it("surfaces a microphone refusal in words, with the recovery in the same sentence", async () => {
    recorderState.failStartWith = {
      code: "denied",
      message: "Microphone access is blocked. Allow it in your browser's site settings, then try again.",
    };
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText(/no voice posts yet/i)).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /record a voice post/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/microphone access is blocked/i);
    // And the control is still there: the failure has a way forward.
    expect(screen.getByRole("button", { name: /record a voice post/i })).toBeInTheDocument();
  });
});

describe("Voices — playback coordination", () => {
  async function seedTwo(): Promise<VoicePost[]> {
    const first = await backendRef.current!.create(draft());
    await new Promise((r) => setTimeout(r, 5));
    const second = await backendRef.current!.create(draft());
    return [first, second];
  }

  it("playing a voice pauses the feed player", async () => {
    await seedTwo();
    const user = userEvent.setup();
    renderPage();
    const playButtons = await screen.findAllByRole("button", { name: /^play voice post$/i });
    expect(playButtons).toHaveLength(2);

    await user.click(playButtons[1]!);
    expect(playerState.pause).toHaveBeenCalled();
  });

  it("plays one voice at a time: the first yields when the second starts", async () => {
    await seedTwo();
    const user = userEvent.setup();
    renderPage();
    const playButtons = await screen.findAllByRole("button", { name: /^play voice post$/i });

    await user.click(playButtons[0]!);
    // The button that is now playing offers Pause; the other still offers Play.
    expect(screen.getAllByRole("button", { name: /^pause voice post$/i })).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: /^play voice post$/i }));
    // Still exactly one pause on screen — the first voice stopped.
    expect(screen.getAllByRole("button", { name: /^pause voice post$/i })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /^play voice post$/i })).toHaveLength(1);
  });

  it("the feed wins: a feed clip starting stops the voice", async () => {
    await seedTwo();
    const user = userEvent.setup();
    const { rerender } = renderPage();
    const playButtons = await screen.findAllByRole("button", { name: /^play voice post$/i });
    await user.click(playButtons[0]!);
    expect(screen.getAllByRole("button", { name: /^pause voice post$/i })).toHaveLength(1);

    playerState.currentClip = { id: "feed-clip-1" };
    rerender();

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /^pause voice post$/i })).not.toBeInTheDocument();
    });
    expect(screen.getAllByRole("button", { name: /^play voice post$/i })).toHaveLength(2);
  });
});

describe("Voices — likes and deletes", () => {
  it("toggles a like and its count through the store", async () => {
    await backendRef.current!.create(draft());
    const user = userEvent.setup();
    renderPage();

    const like = await screen.findByRole("button", { name: /like voice post, 0 likes/i });
    expect(like).toHaveAttribute("aria-pressed", "false");

    await user.click(like);
    await waitFor(() => expect(like).toHaveAttribute("aria-pressed", "true"));
    expect(like).toHaveAccessibleName("Like voice post, 1 like");
  });

  it("delete asks for confirmation, then removes the post", async () => {
    await backendRef.current!.create(draft());
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("article");

    await user.click(screen.getByRole("button", { name: /delete voice post/i }));
    expect(screen.getByRole("button", { name: /confirm delete/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /confirm delete/i }));
    await waitFor(() => expect(screen.queryByRole("article")).not.toBeInTheDocument());
    expect(screen.getByText(/no voice posts yet/i)).toBeInTheDocument();
  });
});

describe("Voices — the thread", () => {
  it("opens a post's replies, records one, and the count follows", async () => {
    const parent = await backendRef.current!.create(draft());
    const user = userEvent.setup();
    renderPage();

    const replies = await screen.findByRole("button", { name: /0 replies/i });
    await user.click(replies);

    // The thread: a way back, the parent, and a reply composer.
    expect(screen.getByRole("button", { name: /all voice posts/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^0 replies$/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /record a reply/i }));
    await user.click(screen.getByRole("button", { name: /stop recording/i }));
    await user.click(screen.getByRole("button", { name: /^post voice$/i }));

    // One reply card on screen (the parent is an article too — look inside the thread).
    await waitFor(() => expect(screen.getByRole("heading", { name: /^1 reply$/i })).toBeInTheDocument());
    expect(screen.getAllByRole("article")).toHaveLength(2);
    expect(await backendRef.current!.get(parent.id)).toMatchObject({ replyCount: 1 });
  });

  it("deleting the parent closes the thread and refreshes the list", async () => {
    await backendRef.current!.create(draft());
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /0 replies/i }));
    // The parent card in the thread owns its own delete.
    const thread = screen.getByRole("region", { name: /voice post thread/i }) ?? document.body;
    await user.click(within(thread as HTMLElement).getByRole("button", { name: /delete voice post/i }));
    await user.click(within(thread as HTMLElement).getByRole("button", { name: /confirm delete/i }));

    await waitFor(() => expect(screen.getByText(/no voice posts yet/i)).toBeInTheDocument());
  });
});
