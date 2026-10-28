import React, { useEffect, useRef, useState } from "react";
import { X, Check, Copy, Send, Radio, Search } from "lucide-react";
import { shareAPI } from "../../api/client";
import { FeedClip } from "../../types/echoflow";

interface ShareModalProps {
  clip: FeedClip | null;
  isOpen: boolean;
  onClose: () => void;
}

interface PeerUser {
  id: number;
  username: string;
}

const DIALOG_TITLE_ID = "share-modal-title";
const USERNAME_INPUT_ID = "share-modal-username";
const USERNAME_ERROR_ID = "share-modal-username-error";
const SEND_ERROR_ID = "share-modal-send-error";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * One key per (clip, recipient).
 *
 * `Feed.tsx:179-183` mounts this component once and only ever flips `isOpen`,
 * so every `useState` here outlives a close — the component returns `null`, it
 * is never unmounted. Keying on the recipient alone therefore marked the *next*
 * clip's row "Sent" after a share for the previous one. Clip-scoping the key
 * also means a request that resolves after the modal has been closed and
 * reopened writes a key nothing reads, instead of poisoning the new clip.
 */
const shareKey = (clipId: string, recipientId: number): string => `${clipId}:${recipientId}`;

/** The HTTP status on an `apiRequest` rejection, or `undefined` if there was no response. */
function httpStatus(err: unknown): number | undefined {
  if (typeof err !== "object" || err === null) return undefined;
  const status = (err as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}

/**
 * A message the server actually meant for a human.
 *
 * `apiRequest` falls back to the literal "Request failed", and for a 5xx with
 * `DEBUG=False` it puts the entire HTML error page in `message`. Neither is a
 * diagnosis, so neither may be shown as one.
 */
function serverMessage(err: unknown): string | null {
  if (typeof err !== "object" || err === null) return null;
  const message = (err as { message?: unknown }).message;
  if (typeof message !== "string") return null;
  const trimmed = message.trim();
  if (!trimmed || trimmed === "Request failed" || trimmed.startsWith("<")) return null;
  return trimmed;
}

/**
 * `find-user` failures, by cause. The old handler mapped every one of them —
 * 400, 404, 429, 500 and a `TypeError: Failed to fetch` — to the single
 * sentence "Peer listener not found in directory.", which is false in four of
 * those five cases and tells the user their username is wrong when the real
 * problem is their connection.
 */
function lookupFailureMessage(err: unknown): string {
  const status = httpStatus(err);
  if (status === undefined) {
    return "Could not reach the server. Check your connection, then search again.";
  }
  if (status === 404) {
    return serverMessage(err) ?? "No listener with that username.";
  }
  if (status === 429) {
    return "Too many searches. Wait a moment, then try again.";
  }
  if (status === 400) {
    return serverMessage(err) ?? "The server rejected that search. Check the username.";
  }
  return "The server could not complete that search. Try again in a moment.";
}

/** `send-share` failures, by cause. A failed send must never read as "Sent". */
function sendFailureMessage(err: unknown): string {
  const status = httpStatus(err);
  if (status === undefined) {
    return "Could not reach the server. The clip was not sent.";
  }
  if (status === 429) {
    return "Too many shares. Wait a moment, then try again.";
  }
  if (status === 403) {
    return serverMessage(err) ?? "This clip may not be shared.";
  }
  if (status === 400 || status === 404) {
    return serverMessage(err) ?? "The server rejected this share. Try a different listener.";
  }
  return "The clip could not be sent. Try again in a moment.";
}

export const ShareModal: React.FC<ShareModalProps> = ({ clip, isOpen, onClose }) => {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [sentShares, setSentShares] = useState<Record<string, boolean>>({});
  const [pendingShares, setPendingShares] = useState<Record<string, boolean>>({});
  const [sendError, setSendError] = useState<string | null>(null);
  const [searchUsername, setSearchUsername] = useState<string>("");
  const [foundUser, setFoundUser] = useState<PeerUser | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState<boolean>(false);

  const dialogRef = useRef<HTMLDivElement | null>(null);
  const copyResetTimer = useRef<number | null>(null);

  // Nothing in this dialog may outlive a close: the instance is mounted once
  // for the lifetime of the feed, so the previous clip's "Sent" rows, the
  // previous search term and a stale error would all be waiting on reopen.
  useEffect(() => {
    if (!isOpen) return;
    setCopyState("idle");
    setSentShares({});
    setPendingShares({});
    setSendError(null);
    setSearchUsername("");
    setFoundUser(null);
    setSearchError(null);
    setIsSearching(false);
  }, [isOpen, clip?.id]);

  // Move focus into the dialog on open and put it back on the trigger on close,
  // so a keyboard user is not left tabbing through the feed behind the overlay.
  useEffect(() => {
    if (!isOpen) return;
    const previouslyFocused = document.activeElement;
    dialogRef.current?.focus();
    return () => {
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  }, [isOpen, clip?.id]);

  useEffect(
    () => () => {
      if (copyResetTimer.current !== null) window.clearTimeout(copyResetTimer.current);
    },
    [],
  );

  const handleDialogKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    const first = focusable.at(0);
    const last = focusable.at(focusable.length - 1);
    if (!first || !last) {
      event.preventDefault();
      return;
    }

    const active = document.activeElement;
    if (event.shiftKey) {
      if (active === first || active === dialog) {
        event.preventDefault();
        last.focus();
      }
      return;
    }
    if (active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const handleCopyLink = async () => {
    if (!clip) return;
    const link = `${window.location.origin}/?clip=${clip.id}`;

    // `navigator.clipboard` is `undefined` on a non-secure origin
    // (`http://<LAN-IP>:5173`). The old handler called it unconditionally, so
    // the throw skipped `setCopied` and the button was left mid-transition.
    const clipboard = navigator.clipboard;
    if (typeof clipboard?.writeText !== "function") {
      setCopyState("failed");
      return;
    }

    try {
      // Awaited, and caught. The old call was fire-and-forget, so a permission
      // rejection was an unhandled rejection *and* the button said "Copied".
      await clipboard.writeText(link);
    } catch {
      setCopyState("failed");
      return;
    }

    setCopyState("copied");
    if (copyResetTimer.current !== null) window.clearTimeout(copyResetTimer.current);
    copyResetTimer.current = window.setTimeout(() => setCopyState("idle"), 2000);
  };

  const handleSearchUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchUsername.trim()) return;
    setIsSearching(true);
    setSearchError(null);
    setFoundUser(null);
    setSendError(null);
    try {
      const user = await shareAPI.findUser(searchUsername.trim());
      setFoundUser(user);
    } catch (err: unknown) {
      setSearchError(lookupFailureMessage(err));
      setFoundUser(null);
    } finally {
      setIsSearching(false);
    }
  };

  const handleSendToUser = async (recipientId: number) => {
    if (!clip) return;
    const key = shareKey(clip.id, recipientId);
    // `services/shares.py:31` is an unconditional `ShareEvent.objects.create`
    // with no dedupe, so this guard is the only thing standing between a
    // double-tap and two inbox rows plus two counter bumps. The button is marked
    // `aria-disabled` rather than `disabled` on purpose: a natively disabled
    // button leaves the tab order and drops focus to <body> (WCAG 2.4.3), and
    // the button is focusable again a moment later.
    if (sentShares[key] || pendingShares[key]) return;

    setPendingShares((prev) => ({ ...prev, [key]: true }));
    setSendError(null);
    try {
      await shareAPI.sendShare(clip.id, recipientId);
      setSentShares((prev) => ({ ...prev, [key]: true }));
    } catch (err: unknown) {
      // The old catch was `console.warn` only, so a refused share was
      // indistinguishable from a delivered one.
      setSendError(sendFailureMessage(err));
    } finally {
      setPendingShares((prev) => ({ ...prev, [key]: false }));
    }
  };

  if (!isOpen || !clip) return null;

  const foundKey = foundUser ? shareKey(clip.id, foundUser.id) : "";
  const isFoundUserSent = foundUser ? sentShares[foundKey] === true : false;
  const isFoundUserPending = foundUser ? pendingShares[foundKey] === true : false;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 ">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={DIALOG_TITLE_ID}
        tabIndex={-1}
        onKeyDown={handleDialogKeyDown}
        className="w-full max-w-sm bg-panel border border-line rounded-3xl p-6 shadow-float space-y-5 animate-in zoom-in-95 duration-150"
      >
        {/* Header */}
        <div className="flex items-center justify-between pb-3 border-b border-line">
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-link" />
            <h3
              id={DIALOG_TITLE_ID}
              className="text-sm font-semibold tracking-tight text-fg"
            >
              Dispatch to Peer Queue
            </h3>
          </div>
          <button
            type="button"
            aria-label="Close share dialog"
            onClick={onClose}
            className="p-1 rounded-full text-fg-subtle hover:text-fg"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Clip Summary Preview */}
        <div className="p-3.5 rounded-xl bg-black/60 border border-line flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-accent text-accent-fg flex items-center justify-center font-semibold text-xs tabular-nums">
            {clip.category.slice(0, 2).toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <h4 className="text-xs font-semibold text-fg truncate">{clip.title}</h4>
            <p className="text-[11px] tabular-nums text-fg-subtle truncate">
              @{clip.creator_name} • {clip.category}
            </p>
          </div>
        </div>

        {/* Copy Link Button */}
        <div>
          <button
            type="button"
            onClick={() => void handleCopyLink()}
            className="w-full py-3 px-4 rounded-xl bg-white/5 hover:bg-white/10 border border-line text-xs tabular-nums font-bold text-fg flex items-center justify-between transition-colors"
          >
            <div className="flex items-center gap-2">
              <Copy className="w-4 h-4 text-link" />
              <span>
                {copyState === "copied"
                  ? "Direct Stream URL Copied"
                  : copyState === "failed"
                    ? "Copy Blocked By Browser"
                    : "Copy Audio Reel Link"}
              </span>
            </div>
            {copyState === "copied" && <Check className="w-4 h-4 text-link" />}
          </button>
          {/* The button label alone is not announced when focus is elsewhere,
              and the success case is a swap of text with no live region. */}
          <p role="status" className="mt-1 text-[11px] tabular-nums text-fg-subtle">
            {copyState === "copied" && "Link copied to your clipboard."}
            {copyState === "failed" &&
              "Your browser blocked clipboard access. Copy the address bar instead."}
          </p>
        </div>

        {/* Find Peer Input */}
        <form onSubmit={handleSearchUser} className="space-y-2">
          <label
            htmlFor={USERNAME_INPUT_ID}
            className="text-[11px] tabular-nums text-fg-subtle block"
          >
            Find Listener by Username
          </label>
          <div className="flex items-center gap-2">
            <input
              id={USERNAME_INPUT_ID}
              type="text"
              value={searchUsername}
              onChange={(e) => setSearchUsername(e.target.value)}
              placeholder="e.g. roastmaster"
              aria-invalid={searchError !== null ? true : undefined}
              aria-describedby={searchError !== null ? USERNAME_ERROR_ID : undefined}
              className="flex-1 bg-black border border-control rounded-xl px-3 py-2 text-xs tabular-nums text-fg placeholder-white/20 focus:outline-none focus:border-link"
            />
            <button
              type="submit"
              aria-label="Search for listener"
              disabled={isSearching}
              className="p-2.5 rounded-xl bg-white/10 hover:bg-white/20 text-fg transition-colors"
            >
              <Search className="w-4 h-4" />
            </button>
          </div>
          {searchError && (
            <p id={USERNAME_ERROR_ID} role="alert" className="text-[11px] tabular-nums text-danger">
              {searchError}
            </p>
          )}
        </form>

        {/* Search Result.
            There is no peer, contact or suggestion endpoint in this codebase —
            `GET /share/find-user/?username=` is the only directory, and it is
            exact-match. The four "Network Peers" that used to render here
            carried real `User` primary keys, so one tap on "Stream" wrote a
            `ShareEvent` and bumped the shares counter for a stranger
            (`views/social.py:164` resolves the id with `get_object_or_404`).
            An honest empty state is the only alternative to inventing them. */}
        <div className="space-y-2">
          {foundUser ? (
            <>
              <span className="text-[11px] tabular-nums text-fg-subtle block">
                Discovered Listener
              </span>
              <div className="space-y-2">
                <div className="flex items-center justify-between p-2.5 rounded-xl bg-white/5 border border-line">
                  <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 rounded-full bg-white/10 flex items-center justify-center font-semibold text-xs text-link">
                      {foundUser.username[0]?.toUpperCase()}
                    </div>
                    <span className="text-xs font-semibold text-fg">
                      @{foundUser.username}
                    </span>
                  </div>

                  <button
                    type="button"
                    onClick={() => void handleSendToUser(foundUser.id)}
                    aria-disabled={isFoundUserSent || isFoundUserPending}
                    className={`px-3 py-1 rounded text-[11px] font-semibold transition-all flex items-center gap-1 ${
                      isFoundUserSent
                        ? "bg-accent/20 text-link border border-accent/40"
                        : "bg-accent text-accent-fg hover:bg-accent-hover"
                    }`}
                  >
                    {isFoundUserSent ? (
                      <>
                        <Check className="w-3 h-3" />
                        Sent
                      </>
                    ) : isFoundUserPending ? (
                      "Sending…"
                    ) : (
                      <>
                        <Send className="w-3 h-3" />
                        Stream
                      </>
                    )}
                  </button>
                </div>
                {sendError && (
                  <p id={SEND_ERROR_ID} role="alert" className="text-[11px] tabular-nums text-danger">
                    {sendError}
                  </p>
                )}
              </div>
            </>
          ) : (
            <p className="text-[11px] tabular-nums text-fg-subtle">
              Search for a listener by username to send this clip.
            </p>
          )}
        </div>
      </div>
    </div>
  );
};
