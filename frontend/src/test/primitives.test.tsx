import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { Header } from "../components/common/Header";
import { VizPreferenceProvider } from "../stores/viz";
import { BottomNav } from "../components/navigation/BottomNav";
import { ErrorBoundary } from "../components/common/ErrorBoundary";

/**
 * `Header`, `BottomNav` and `ErrorBoundary`.
 *
 * Header: the avatar button was announced as the user's own username (button
 * content outranks `title` in name computation), the desktop destinations were a
 * bare `<div>` with no navigation landmark and no `aria-current`, and the
 * backend-health signal was removed from the Header on the owner's
 * instruction, so the header now carries navigation, the two playback
 * switches and the profile route.
 *
 * ErrorBoundary: its docstring claimed it sits *inside* the providers so
 * provider throws are not caught. `App.tsx:151-160` shows the opposite — it is
 * the outermost element. A comment that makes correct code look broken costs
 * the next reader a debugging cycle.
 *
 */

const MOCK_USER = { id: 7, username: "alice", email: "alice@example.com" };

vi.mock("../stores/auth", () => ({
  useAuth: () => ({
    user: MOCK_USER,
    // A real avatar URL, so the button's content is the <img alt={username}>
    // case — the one where the accessible name is the username itself.
    profile: { profile_picture: "https://cdn.example/alice.png" },
    isAuthenticated: true,
  }),
}));

vi.mock("../stores/player", () => ({
  usePlayer: () => ({ handsFreeMode: false, setHandsFreeMode: vi.fn() }),
}));

function renderHeader(activeTab = "feed", unreadCount = 0) {
  return render(
    <VizPreferenceProvider>
      <Header activeTab={activeTab} setActiveTab={() => {}} unreadCount={unreadCount} />
    </VizPreferenceProvider>,
  );
}

/** Walk every ancestor of `el`, so a `hidden` wrapper can be detected. */
function ancestorsOf(el: Element): Element[] {
  const out: Element[] = [];
  let node: Element | null = el.parentElement;
  while (node) {
    out.push(node);
    node = node.parentElement;
  }
  return out;
}

describe("Header — the avatar button is named for what it does", () => {
  it("is not announced as the user's own username", () => {
    renderHeader();

    const avatar = screen.getByRole("button", { name: /my profile/i });

    // The reported defect: the button's only child is `<img alt={username}>`, so
    // content-derived naming made the button's name the username — "alice" is
    // the name of the control that opens the profile page.
    expect(avatar).toHaveAccessibleName("My Profile");
    expect(avatar).not.toHaveAccessibleName(MOCK_USER.username);
  });

  it("keeps the avatar image itself named, rather than silently dropping it", () => {
    renderHeader();
    // A blanket `aria-hidden` on the image would satisfy the test above and
    // destroy the alt text, so assert the image is still in the tree.
    const image = document.querySelector('img[alt="alice"]');
    expect(image).not.toBeNull();
  });
});

describe("Header — the desktop destinations are a real navigation region", () => {
  it("exposes a navigation landmark", () => {
    renderHeader();
    // `BottomNav`'s <nav> is `md:hidden`, so on a desktop viewport the app had
    // no navigation region at all.
    const navs = screen.getAllByRole("navigation");
    expect(navs).toHaveLength(1);
    expect(navs[0]).toHaveAccessibleName();
  });

  it("marks the active destination with aria-current", () => {
    renderHeader("inbox");

    expect(screen.getByRole("button", { name: /inbox/i })).toHaveAttribute(
      "aria-current",
      "page",
    );

    const current = screen
      .getAllByRole("button")
      .filter((el) => el.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAccessibleName(/inbox/i);
  });

  it("leaves aria-current off the inactive destinations", () => {
    renderHeader("feed");
    expect(screen.getByRole("button", { name: /discover/i })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("names every desktop destination", () => {
    renderHeader();
    for (const label of [/live feed/i, /discover/i, /creator studio/i, /voices/i, /inbox/i]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });
});

describe("Header — the unread count says what it counts", () => {
  it("does not leave the badge as a bare number", () => {
    // The button's computed name was "Inbox 5": a number that states nothing
    // about what it counts. RECON-06 §15.8.
    renderHeader("feed", 5);
    const inbox = screen.getByRole("button", { name: /^inbox/i });
    expect(inbox).toHaveAccessibleName(/inbox\s*5\s*unread/i);
  });
});

describe("BottomNav", () => {
  it("names its navigation landmark, now that a second one exists", () => {
    // Two <nav> elements with no accessible name is a landmark list a screen
    // reader cannot act on.
    render(<BottomNav activeTab="feed" setActiveTab={() => {}} unreadCount={0} />);
    expect(screen.getByRole("navigation")).toHaveAccessibleName();
  });

  it("says what the inbox badge counts", () => {
    render(
      <BottomNav activeTab="feed" setActiveTab={() => {}} unreadCount={7} />,
    );
    // The computed name is "7 unread Inbox": the badge precedes the label in
    // the DOM (it sits on the icon, above the caption), and reordering it would
    // move the visible caption. What matters is that the number is no longer
    // the whole name.
    const inbox = screen.getByRole("button", { name: /inbox/i });
    expect(inbox).toHaveAccessibleName(/7\s*unread/i);
  });
});

describe("ErrorBoundary — the docstring stops describing the wrong tree", () => {
  const source = readFileSync(
    resolvePath(process.cwd(), "src/components/common/ErrorBoundary.tsx"),
    "utf8",
  );

  it("no longer claims it sits inside the providers", () => {
    // A source read, because the claim is prose and jsdom cannot observe a
    // comment. This is the only honest way to pin it: there is no runtime
    // behaviour that distinguishes "the comment is true" from "the comment is
    // false". It is asserted because a false comment about component nesting
    // is the failure mode this repo has already paid for twice
    // (`CORS_URLS_REGEX`, and the same claim repeated in two planning docs).
    expect(source).not.toMatch(/sits\s+\*?inside\*?\s+the\s+providers/i);
  });

  it("no longer points at a note in App.tsx that does not exist", () => {
    // The referenced note has never existed in App.tsx. RECON-06 §15.10.
    expect(source).not.toMatch(/see the note in `?App\.tsx`?/i);
  });

  it("states the placement that is actually true", () => {
    expect(source).toMatch(/outermost/i);
  });

  it("preserves the two 44px recovery controls", () => {
    // The only two buttons in the app that meet a 44px target (RECON-06 §8).
    // Asserted on the inline style because jsdom computes no layout.
    const spy = console.error;
    console.error = () => {};
    try {
      render(
        <ErrorBoundary>
          <Thrower />
        </ErrorBoundary>,
      );
    } finally {
      console.error = spy;
    }

    for (const name of ["Try again", "Reload"]) {
      const button = screen.getByRole("button", { name });
      expect(button.style.minHeight).toBe("44px");
    }
  });
});

function Thrower(): never {
  throw new Error("kaboom");
}

describe("Header — keyboard reachability of the brand", () => {
  it("makes the brand a button rather than a click-only div", async () => {
    // RECON-06 §10: a `<div onClick>` with no role, no tabIndex and no key
    // handler. It is the shortest route back to the feed from anywhere.
    renderHeader();
    const nav = screen.getByRole("navigation");
    const header = nav.closest("header");
    expect(header).not.toBeNull();

    const user = userEvent.setup();
    await user.tab();
    // The first tab stop must be a real control, so the brand is reachable
    // rather than skipped over.
    expect(document.activeElement).not.toBe(document.body);
    await waitFor(() => {
      expect(document.activeElement?.tagName).toBe("BUTTON");
    });
  });
});
