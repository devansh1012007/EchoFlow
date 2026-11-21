import { useEffect, useState } from "react";

/**
 * Reactive `matchMedia` for layout decisions that CSS alone cannot make.
 *
 * The feed rails are the reason this exists: `hidden xl:block` hides an element
 * *visually*, but jsdom does not apply CSS and, more importantly, a
 * CSS-hidden element is still in the accessibility tree unless `display:none`
 * is actually computed. Anything that duplicates an accessible name must be
 * **not rendered** on the wrong viewport, not merely hidden — so the decision
 * has to be a React conditional, which means it has to reach React state.
 *
 * First render reads the query synchronously (no flash of the wrong layout),
 * then subscribes so a resized window or a rotated phone updates it.
 *
 * `false` is the safe default everywhere this is used: `matchMedia` missing
 * entirely (ancient browser) or throwing on a malformed query means "render
 * the phone layout", which is the layout that always works.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
    try {
      return window.matchMedia(query).matches;
    } catch {
      return false;
    }
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    let mql: MediaQueryList;
    try {
      mql = window.matchMedia(query);
    } catch {
      setMatches(false);
      return;
    }
    // The query can change between renders, and `mql.matches` at effect time
    // is more current than the state captured at mount.
    setMatches(mql.matches);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    // `addEventListener` is the standard; the legacy `addListener` path is not
    // polyfilled because the app's browser floor (ES2020) has it.
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}
