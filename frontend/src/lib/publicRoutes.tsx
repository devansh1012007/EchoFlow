import React from "react";
import { SharedClipPage } from "../pages/SharedClip";
import { SupportPage, SUPPORT_PATH } from "../pages/Support";
import { TermsPage, TERMS_PATH } from "../pages/Terms";

/**
 * Pages that render without signing in, outside the app shell.
 *
 * The app has no router: signed-in navigation is tab state in `App.tsx`. These
 * paths are the exception, resolved once at startup from `location.pathname`.
 * Every other path falls through to `<App />`, which is what `public/_redirects`
 * (`/* /index.html 200`) relies on.
 */
const normalize = (pathname: string): string => pathname.replace(/\/+$/, "") || "/";

export const SHARED_CLIP_RE = /^\/clip\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

export function resolvePublicRoute(pathname: string, search = ""): React.ReactElement | null {
  const path = normalize(pathname);
  if (path === TERMS_PATH) return <TermsPage />;
  if (path === SUPPORT_PATH) return <SupportPage />;
  const shared = SHARED_CLIP_RE.exec(path);
  if (shared) return <SharedClipPage clipId={(shared[1] as string).toLowerCase()} shareToken={new URLSearchParams(search).get("s")} />;
  return null;
}
