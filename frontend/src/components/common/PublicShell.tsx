import React from "react";

interface PublicShellProps {
  /** id of the `<main>` region, targeted by the skip link. */
  contentId: string;
  skipLabel: string;
  children: React.ReactNode;
  width?: "prose" | "narrow";
}

/**
 * Layout for pages reachable while signed out (`/terms`, `/support`, `/clip/:id`).
 * One header, one footer, one skip link, so every public page is navigable the same way.
 */
export const PublicShell: React.FC<PublicShellProps> = ({ contentId, skipLabel, children, width = "prose" }) => (
  <div className="min-h-screen bg-canvas text-fg font-sans">
    <a
      href={`#${contentId}`}
      className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-fg"
    >
      {skipLabel}
    </a>
    <header className="border-b border-line">
      <div className={`mx-auto flex items-center justify-between px-5 py-4 ${width === "prose" ? "max-w-2xl" : "max-w-md"}`}>
        <a href="/" className="text-sm font-semibold text-fg transition-colors hover:text-link">
          EchoFlow
        </a>
        <a href="/" className="text-sm text-link hover:underline">
          Back to app
        </a>
      </div>
    </header>
    <main id={contentId} tabIndex={-1} className={`mx-auto space-y-10 px-5 py-10 focus:outline-none ${width === "prose" ? "max-w-2xl" : "max-w-md"}`}>
      {children}
    </main>
    <footer className="border-t border-line">
      <nav aria-label="Legal" className={`mx-auto flex gap-5 px-5 py-6 text-xs ${width === "prose" ? "max-w-2xl" : "max-w-md"}`}>
        <a href="/terms" className="text-link hover:underline">
          Terms
        </a>
        <a href="/support" className="text-link hover:underline">
          Support and grievances
        </a>
      </nav>
    </footer>
  </div>
);
