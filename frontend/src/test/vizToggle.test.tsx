import { beforeEach, describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "@testing-library/react";
import { VizToggle } from "../components/feed/VizToggle";
import { VizPreferenceProvider } from "../stores/viz";
import { VIZ_COOKIE, readVizCookie } from "../lib/vizPreference";

/**
 * `VizToggle` — the brief's "option to turn off the visualization, saving the
 * user setting in a cookie".
 *
 * The cookie mechanics themselves are `vizPreference.test.tsx`'s subject; here
 * what is under test is the control: a stable accessible name, the state on
 * `aria-pressed` (the app-wide toggle-button convention), and that pressing it
 * really persists a choice.
 */

function clearCookie() {
  document.cookie = `${VIZ_COOKIE}=; Max-Age=0; Path=/`;
}

function renderToggle() {
  return render(
    <VizPreferenceProvider>
      <VizToggle />
    </VizPreferenceProvider>,
  );
}

describe("VizToggle", () => {
  beforeEach(clearCookie);

  it("is a toggle button with a stable accessible name", () => {
    renderToggle();
    const button = screen.getByRole("button", { name: "Sound wave" });
    expect(button).toHaveAttribute("aria-pressed", "true");
  });

  it("pressing it turns the wave off and writes the cookie", async () => {
    const user = userEvent.setup();
    renderToggle();
    await user.click(screen.getByRole("button", { name: "Sound wave" }));
    expect(screen.getByRole("button", { name: "Sound wave" })).toHaveAttribute("aria-pressed", "false");
    expect(readVizCookie()).toBe("off");
  });

  it("starts off when the stored choice says off", () => {
    document.cookie = `${VIZ_COOKIE}=off; Max-Age=31536000; Path=/; SameSite=Lax`;
    renderToggle();
    expect(screen.getByRole("button", { name: "Sound wave" })).toHaveAttribute("aria-pressed", "false");
  });
});
