import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { VIZ_COOKIE, readVizCookie, resolveVizPreference, writeVizCookie } from "../lib/vizPreference";
import { VizPreferenceProvider, useVizPreference } from "../stores/viz";

function clearCookie() {
  document.cookie = `${VIZ_COOKIE}=; Max-Age=0; Path=/`;
}

describe("viz preference cookie", () => {
  beforeEach(clearCookie);

  it("reads null when no choice was made", () => {
    expect(readVizCookie()).toBeNull();
  });

  it("round-trips a written choice", () => {
    writeVizCookie("off", document, false);
    expect(readVizCookie()).toBe("off");
    writeVizCookie("on", document, false);
    expect(readVizCookie()).toBe("on");
  });

  it("ignores a tampered value instead of trusting it", () => {
    document.cookie = `${VIZ_COOKIE}=maybe; Path=/`;
    expect(readVizCookie()).toBeNull();
  });

  it("does not confuse a cookie whose name merely contains ef_viz", () => {
    document.cookie = `x_${VIZ_COOKIE}=off; Path=/`;
    expect(readVizCookie()).toBeNull();
    document.cookie = `x_${VIZ_COOKIE}=; Max-Age=0; Path=/`;
  });

  it("writes a one-year, lax, root-path cookie, adding Secure only on https", () => {
    const jar = { cookie: "" };
    writeVizCookie("off", jar, true);
    expect(jar.cookie).toMatch(/^ef_viz=off; Max-Age=31536000; Path=\/; SameSite=Lax; Secure$/);
    writeVizCookie("off", jar, false);
    expect(jar.cookie).not.toMatch(/Secure/);
  });

  it("a stored choice beats reduced motion; with no choice, reduced motion starts off", () => {
    expect(resolveVizPreference("on", true)).toBe("on");
    expect(resolveVizPreference("off", false)).toBe("off");
    expect(resolveVizPreference(null, true)).toBe("off");
    expect(resolveVizPreference(null, false)).toBe("on");
  });
});

function Probe() {
  const { viz, toggleViz } = useVizPreference();
  return <button onClick={toggleViz}>wave {viz}</button>;
}

describe("VizPreferenceProvider", () => {
  beforeEach(clearCookie);

  it("starts from the cookie and persists a toggle", () => {
    writeVizCookie("off", document, false);
    render(<VizPreferenceProvider><Probe /></VizPreferenceProvider>);
    expect(screen.getByRole("button")).toHaveTextContent("wave off");
    act(() => screen.getByRole("button").click());
    expect(screen.getByRole("button")).toHaveTextContent("wave on");
    expect(readVizCookie()).toBe("on");
  });

  it("does not write a cookie until the person chooses", () => {
    render(<VizPreferenceProvider><Probe /></VizPreferenceProvider>);
    expect(readVizCookie()).toBeNull();
  });
});
