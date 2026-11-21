import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { readVizCookie, resolveVizPreference, writeVizCookie, type VizPreference } from "../lib/vizPreference";

interface VizContextValue {
  /** "on" draws the wave; "off" draws a still image (cover or a seeded static wave). */
  viz: VizPreference;
  vizOn: boolean;
  setViz: (next: VizPreference) => void;
  toggleViz: () => void;
}

const VizContext = createContext<VizContextValue | null>(null);

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

export function VizPreferenceProvider({ children }: { children: ReactNode }) {
  const [viz, setVizState] = useState<VizPreference>(() => resolveVizPreference(readVizCookie(), prefersReducedMotion()));

  const setViz = useCallback((next: VizPreference) => {
    setVizState(next);
    writeVizCookie(next); // only a deliberate choice is persisted
  }, []);

  const value = useMemo<VizContextValue>(
    () => ({ viz, vizOn: viz === "on", setViz, toggleViz: () => setViz(viz === "on" ? "off" : "on") }),
    [viz, setViz],
  );
  return <VizContext.Provider value={value}>{children}</VizContext.Provider>;
}

export function useVizPreference(): VizContextValue {
  const ctx = useContext(VizContext);
  if (!ctx) throw new Error("useVizPreference must be used inside <VizPreferenceProvider>");
  return ctx;
}
