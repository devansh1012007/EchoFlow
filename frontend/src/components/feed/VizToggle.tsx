import { AudioWaveform } from "lucide-react";
import { useVizPreference } from "../../stores/viz";

/**
 * The wave on/off switch from the brief: "an option to turn off the
 * visualization, saving the user setting in a cookie". `useVizPreference`
 * owns the cookie; this is only the control.
 *
 * Naming follows the like button's convention (`ReelCard.tsx`): a stable label
 * that names the thing, `aria-pressed` carrying the state. Changing the label
 * to "Turn off wave" would announce the state twice and contradict the APG
 * toggle-button pattern the app already uses.
 *
 * The visible word stays "Wave" at every width; the icon is decorative. The
 * `.tap-target` floor (44px) applies because this is an icon-adjacent compact
 * control in the header row.
 */
export function VizToggle() {
  const { vizOn, toggleViz } = useVizPreference();

  return (
    <button
      type="button"
      onClick={toggleViz}
      aria-pressed={vizOn}
      aria-label="Sound wave"
      title={vizOn ? "Sound wave is on" : "Sound wave is off"}
      className={`tap-target flex items-center gap-1.5 px-2.5 rounded-lg text-[11px] font-semibold transition-colors border ${
        vizOn
          ? "bg-accent text-accent-fg border-accent"
          : "bg-white/5 text-fg-muted hover:text-fg border-line hover:bg-white/10"
      }`}
    >
      <AudioWaveform className="w-3.5 h-3.5" aria-hidden="true" />
      <span className="hidden sm:inline">Wave</span>
    </button>
  );
}
