import React from "react";
import { Headphones } from "lucide-react";
import { usePlayer } from "../../stores/player";
import { useAuth } from "../../stores/auth";
import { VizToggle } from "../feed/VizToggle";

interface HeaderProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  unreadCount: number;
}

/**
 * The shell's top bar: the wordmark, the desktop navigation, the two playback
 * switches (hands-free, sound wave) and the profile route.
 *
 * The backend-health indicator that used to sit here ("System Status /
 * Backend Ready / Not Reachable") was removed on the owner's instruction: it
 * was operator chrome on a listener's app, and its green state asserted a
 * liveness probe the listener has no action for. The probe hook
 * (`useBackendHealth`) went with it; nothing else consumed it.
 *
 * Navigation is five destinations — Live Feed, Discover, Creator Studio,
 * Voices, Inbox — with Profile reachable through the avatar on the right, the
 * same rule `BottomNav` follows on a phone.
 */
export const Header: React.FC<HeaderProps> = ({ activeTab, setActiveTab, unreadCount }) => {
  const { handsFreeMode, setHandsFreeMode } = usePlayer();
  const { user, profile } = useAuth();

  const navLinks = [
    { id: "feed", label: "Live Feed" },
    { id: "explore", label: "Discover" },
    { id: "upload", label: "Creator Studio" },
    { id: "voices", label: "Voices" },
    { id: "inbox", label: "Inbox", badge: unreadCount },
  ];

  return (
    <header className="sticky top-0 z-30 w-full bg-canvas border-b border-line px-4 md:px-8 py-3 transition-all">
      <div className="max-w-7xl mx-auto flex items-center justify-between gap-4">
        {/* Brand mark.
            A <button>, not a <div onClick>: this is the shortest route back to
            the feed from anywhere in the app, and as a div it had no role, no
            tabIndex and no key handler, so it was unreachable by keyboard
            (RECON-06 §10). `type="button"` keeps it from submitting anything.

            The wordmark is Archivo at weight 800, expanded — the display face
            the headings use, so the brand and the content speak with one
            voice. `alt` is empty because the wordmark beside it already names
            the brand; a non-empty alt would announce "EchoFlow" twice. */}
        <button
          type="button"
          onClick={() => setActiveTab("feed")}
          className="flex items-center gap-3 cursor-pointer select-none group text-left"
        >
          <img
            src="/logo.png"
            alt=""
            width={32}
            height={32}
            className="w-8 h-8 rounded-[17%] transition-transform group-hover:scale-105"
          />
          <div>
            <span
              className="block text-xl md:text-2xl leading-none text-fg"
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: 800,
                fontStretch: "112%",
                letterSpacing: "-0.01em",
              }}
            >
              EchoFlow
            </span>
            <p className="text-[11px] tabular-nums text-fg-subtle leading-none mt-1">
              Audio-First Short-Form
            </p>
          </div>
        </button>

        {/* Center Desktop Navigation Tabs.
            Wrapped in a <nav>: on a desktop viewport these five destinations
            are the entire navigation. Labelled because `BottomNav` renders a
            second navigation landmark below `md`, and two unlabelled ones are
            a list a screen reader cannot act on. `aria-current` marks the page
            the user is on. */}
        <nav
          aria-label="Primary"
          className="hidden md:flex items-center gap-5 text-xs font-bold text-fg-subtle"
        >
          {navLinks.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                aria-current={isActive ? "page" : undefined}
                className={`relative py-1.5 transition-colors hover:text-fg flex items-center gap-1.5 ${
                  isActive ? "text-link" : ""
                }`}
              >
                <span>{tab.label}</span>
                {tab.badge && tab.badge > 0 ? (
                  <>
                    <span
                      aria-hidden="true"
                      className="px-1.5 rounded-full bg-accent text-accent-fg text-[11px] tabular-nums font-bold"
                    >
                      {tab.badge}
                    </span>
                    {/* The badge alone is a number that states nothing about what
                        it counts, and it changed on a 30s poll that no live region
                        announced. The visible chip is decorative and the count is
                        spelled out for assistive technology. */}
                    <span className="sr-only">{tab.badge} unread</span>
                  </>
                ) : null}
                {isActive && (
                  <span className="absolute -bottom-0.5 left-0 right-0 h-0.5 bg-accent" />
                )}
              </button>
            );
          })}
        </nav>

        {/* Right Actions: the two playback switches and the profile route. */}
        <div className="flex items-center gap-2 md:gap-3">
          {/* Hands-Free Toggle. Kept from the previous header: it gates the
              feed's autoplay and auto-advance (`ReelList`), and the feed page
              labels the mode it is in. */}
          <button
            type="button"
            onClick={() => setHandsFreeMode(!handsFreeMode)}
            aria-pressed={handsFreeMode}
            className={`tap-target flex items-center gap-1.5 px-2.5 rounded-lg text-[11px] font-bold transition-colors border ${
              handsFreeMode
                ? "bg-accent text-accent-fg border-accent"
                : "bg-white/5 text-fg-muted hover:text-fg border-line hover:bg-white/10"
            }`}
            title="Toggle Hands-Free Continuous Playback"
          >
            <Headphones className="w-3.5 h-3.5" aria-hidden="true" />
            <span className="hidden sm:inline">Hands-Free</span>
            {handsFreeMode && (
              <span aria-hidden="true" className="w-1.5 h-1.5 rounded-full bg-black" />
            )}
          </button>

          {/* The sound wave on/off switch. The wave is the reel's backup
              imagery; a person who would rather not have it moving can turn it
              off, and the choice survives the session in a cookie. */}
          <VizToggle />

          {/* User Profile Avatar.
              `aria-label` rather than relying on `title`: accessible-name
              computation is aria-labelledby → aria-label → content → title, so
              the only child here — `<img alt={user?.username}>`, or the initial
              letter — supplied the name and the button was announced as the
              user's own username. The alt text is left intact rather than
              `aria-hidden`, so the avatar is still described when the button is
              reached by its label. The avatar is the one Profile route on a
              phone (`BottomNav` carries the other five destinations). */}
          <button
            type="button"
            onClick={() => setActiveTab("profile")}
            aria-label="My Profile"
            className="tap-target flex items-center gap-2 p-1 rounded-full hover:ring-2 hover:ring-link/50 transition-all"
            title="My Profile"
          >
            <div className="w-8 h-8 rounded-full bg-white/10 border border-line-strong overflow-hidden flex items-center justify-center">
              {profile?.profile_picture ? (
                <img
                  src={profile.profile_picture}
                  alt={user?.username || "avatar"}
                  className="w-full h-full object-cover"
                />
              ) : (
                <span className="font-bold text-xs text-link">
                  {user?.username?.[0]?.toUpperCase() || "E"}
                </span>
              )}
            </div>
          </button>
        </div>
      </div>
    </header>
  );
};
