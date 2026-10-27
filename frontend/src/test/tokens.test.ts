import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * `styles/tokens.css` is the only design system in the repo, and every
 * assertion here is a read of its source text.
 *
 * IMPORTANT — what these tests can and cannot prove. Tailwind is not
 * processed under vitest, and jsdom has no layout engine and no cascade, so
 * nothing in this file measures a computed style, a used value, or a painted
 * pixel. `expect(css).toMatch(...)` proves a rule is *written*; it cannot prove
 * the rule wins, that a class was applied by a component, or that anything is
 * visible to a user. The contrast test is the one exception: it evaluates the
 * WCAG relative-luminance formula in JS on the hex values parsed out of the
 * file, which is real arithmetic on the real token values rather than a proxy
 * for it. Everything else is a structural pin, and is labelled as one.
 */

// `import.meta.url` is an http URL under vitest's jsdom environment, so the
// module URL cannot be handed to `readFileSync`. `process.cwd()` is the vitest
// root (`frontend/`, where vitest.config.ts lives).
const CSS_PATH = resolve(process.cwd(), "src/styles/tokens.css");
const css = readFileSync(CSS_PATH, "utf8");

/** Every declared custom property name, e.g. `--tap-target`. */
function declaredTokens(): string[] {
  return [...css.matchAll(/^\s*(--[\w-]+)\s*:/gm)].map((m) => m[1] as string);
}

/** The hex value a token resolves to, following one `var(--alias)` hop. */
function tokenHex(name: string): string {
  const direct = new RegExp(`^\\s*${name}\\s*:\\s*(#[0-9a-fA-F]{3,8})\\s*;`, "m").exec(css);
  if (direct) return direct[1] as string;
  const alias = new RegExp(`^\\s*${name}\\s*:\\s*var\\((--[\\w-]+)\\)\\s*;`, "m").exec(css);
  if (alias) return tokenHex(alias[1] as string);
  throw new Error(`${name} does not resolve to a hex colour`);
}

function srgb(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex: string): number {
  const h = hex.replace("#", "");
  const full =
    h.length === 3
      ? h
          .split("")
          .map((c) => c + c)
          .join("")
      : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
}

function contrast(fg: string, bg: string): number {
  const a = relativeLuminance(fg);
  const b = relativeLuminance(bg);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

/** Deep black. Every other surface in the system is lighter than this. */
const APP_BACKGROUND = tokenHex("--color-canvas");
/** The four surfaces, darkest to lightest — text and controls must read on all of them. */
const SURFACES = ["--color-canvas", "--color-panel", "--color-raised", "--color-high"].map(tokenHex);

/** The body of the first `@media (prefers-reduced-motion: reduce)` block. */
function reducedMotionBlock(): string {
  const open = css.indexOf("@media (prefers-reduced-motion: reduce)");
  if (open === -1) return "";
  const braceStart = css.indexOf("{", open);
  let depth = 0;
  for (let i = braceStart; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(braceStart + 1, i);
    }
  }
  return "";
}

describe("tokens.css — the tap-target token is wired, not dead", () => {
  it("declares a .tap-target utility in terms of the --tap-target token", () => {
    // `--tap-target: 64px` was declared and referenced nowhere in the app, so
    // the one value that was supposed to govern every hit area governed none.
    const rule = /\.tap-target\s*\{([^}]*)\}/.exec(css);
    expect(rule).not.toBeNull();
    expect(rule?.[1]).toMatch(/var\(\s*--tap-target\s*\)/);
    // Both axes: a control that is wide but 4px tall is not a target.
    expect(rule?.[1]).toMatch(/min-(block|height)-size/);
    expect(rule?.[1]).toMatch(/min-(inline|width)-size/);
  });

  it("no longer leaves --tap-target referenced by nothing", () => {
    const uses = [...css.matchAll(/var\(\s*--tap-target\s*\)/g)];
    expect(uses.length).toBeGreaterThanOrEqual(1);
  });
});

describe("tokens.css — a global focus-visible indicator", () => {
  it("draws a visible indicator on keyboard focus", () => {
    // The app had 11 `focus:outline-none` declarations, which deleted the
    // browser's focus ring outright, and no rule anywhere replaced it. A global
    // rule is the only way the files that removed theirs can inherit one.
    const rules = [...css.matchAll(/([^{}]*:focus-visible[^{}]*)\{([^}]*)\}/g)];
    expect(rules.length).toBeGreaterThan(0);

    const bodies = rules.map((r) => r[2] as string).join("\n");
    expect(bodies).toMatch(/outline\s*:\s*2px solid/);
    // The one thing this must never do is re-suppress the ring.
    expect(bodies).not.toMatch(/outline\s*:\s*(none|0)\b/);
  });

  it("covers the form controls whose focus:outline-none was removed", () => {
    const selector = [...css.matchAll(/([^{}]*:focus-visible[^{}]*)\{/g)]
      .map((m) => m[1] as string)
      .join(",");
    for (const element of ["button", "input", "select", "textarea", "a"]) {
      expect(selector).toMatch(new RegExp(`\\b${element}\\b`));
    }
  });

  it("uses a ring colour that is visible on the app's darkest surface", () => {
    // A focus indicator has to clear 3:1 against what it sits on (WCAG 1.4.11),
    // or it is present in the DOM and invisible to the person using it.
    // `--focus-ring` is an alias, so resolve it rather than parse a literal.
    const ring = tokenHex("--focus-ring");
    for (const surface of SURFACES) {
      expect(contrast(ring, surface), `focus ring on ${surface}`).toBeGreaterThanOrEqual(3);
    }
    // The brief assigns focus indicators to blue: the ring must be a blue.
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(ring.slice(i, i + 2), 16)) as [number, number, number];
    expect(b, "focus ring is blue-dominant").toBeGreaterThan(Math.max(r, g));
  });
});

describe("tokens.css — prefers-reduced-motion", () => {
  it("has a prefers-reduced-motion block at all", () => {
    // Zero occurrences existed anywhere in src/. WCAG 2.3.3 is AAA, but 2.2.2
    // Pause/Stop/Hide is A, and the feed's auto-advance has no stop control.
    expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(reducedMotionBlock()).not.toBe("");
  });

  it("overrides the ungated smooth scroll, which is the motion users feel most", () => {
    // `html { scroll-behavior: smooth }` is unconditional and applies to every
    // scroll in the app, including the feed's auto-advance scroll.
    expect(css).toMatch(/html\s*\{[^}]*scroll-behavior:\s*smooth/);
    expect(reducedMotionBlock()).toMatch(/scroll-behavior:\s*auto/);
  });

  it("stops the animations rather than merely shortening them", () => {
    const block = reducedMotionBlock();
    // `animation-iteration-count: 1` is what kills `.skeleton`'s infinite
    // `shimmer` and Tailwind's `animate-pulse` / `animate-spin`; overriding the
    // duration alone would leave a `pulse` that is merely very fast.
    expect(block).toMatch(/animation-iteration-count\s*:\s*1/);
    expect(block).toMatch(/animation-duration/);
    expect(block).toMatch(/transition-duration/);
  });
});

describe("tokens.css — the category palette is wired", () => {
  const cats = [
    "--cat-instrumental",
    "--cat-funny",
    "--cat-news",
    "--cat-science",
    "--cat-music",
    "--cat-neutral",
  ];

  it("references every --cat-* token from a rule", () => {
    // All six were declared and used nowhere: `AudioClip.category` chips were
    // painted from a hardcoded brand hex instead.
    for (const cat of cats) {
      expect(css, `${cat} is declared but referenced by no rule`).toMatch(
        new RegExp(`var\\(\\s*${cat}\\s*\\)`),
      );
    }
  });

  it("keeps the keys byte-identical to the stored category strings", () => {
    // `/suggestions/?category=` matches by exact string, so a renamed key would
    // silently stop matching. The class names are the keys.
    for (const cat of cats) {
      const key = cat.replace("--cat-", "");
      expect(css).toMatch(new RegExp(`\\.cat-${key}\\b`));
    }
  });
});

describe("tokens.css — the palette is closed", () => {
  const ALLOWED = [
    "--color-transparent", "--color-current", "--color-white", "--color-black",
    "--color-canvas", "--color-panel", "--color-raised", "--color-high",
    "--color-fg", "--color-mist-300", "--color-fg-muted", "--color-fg-subtle",
    "--color-line", "--color-line-strong", "--color-control",
    "--color-accent", "--color-accent-hover", "--color-accent-fg",
    "--color-link", "--color-link-strong",
    "--color-danger", "--color-danger-solid", "--color-danger-solid-hover", "--color-danger-fg",
  ];

  it("switches Tailwind's default palette off", () => {
    // Without this, `bg-green-500` would still generate CSS and a seventh hue
    // could return without anyone touching the tokens.
    expect(css).toMatch(/--color-\*\s*:\s*initial\s*;/);
  });

  it("declares exactly the named colour tokens and no others", () => {
    const declared = declaredTokens().filter((n) => n.startsWith("--color-") && n !== "--color-*");
    expect([...new Set(declared)].sort()).toEqual([...ALLOWED].sort());
  });

  it("keeps surfaces strictly ordered from deep black upward", () => {
    const lum = SURFACES.map(relativeLuminance);
    expect(SURFACES[0]).toBe("#000000");
    expect([...lum].sort((a, b) => a - b)).toEqual(lum);
  });

  it("keeps grays neutral — no tint hiding in a 'gray'", () => {
    for (const name of ["--color-panel", "--color-raised", "--color-high", "--color-fg-muted", "--color-fg-subtle", "--color-line", "--color-line-strong", "--color-control", "--color-mist-300"]) {
      const h = tokenHex(name).replace("#", "");
      expect(h.slice(0, 2), name).toBe(h.slice(2, 4));
      expect(h.slice(2, 4), name).toBe(h.slice(4, 6));
    }
  });
});

describe("tokens.css — contrast of every role pairing", () => {
  const textOnSurfaces = [
    "--color-fg", "--color-mist-300", "--color-fg-muted", "--color-fg-subtle",
    "--color-link", "--color-link-strong", "--color-danger",
    "--cat-instrumental", "--cat-funny", "--cat-news", "--cat-science", "--cat-music", "--cat-neutral",
  ];

  it("every text colour clears 4.5:1 on deep black, dark gray and raised gray", () => {
    // `high` is the lightest surface and only hosts transient chrome (menus,
    // pressed states), so it is checked at its own floor below.
    for (const token of textOnSurfaces) {
      for (const surface of SURFACES.slice(0, 3)) {
        expect(contrast(tokenHex(token), surface), `${token} on ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("primary, secondary and link text also clear 4.5:1 on the lightest surface", () => {
    const high = SURFACES[3] as string;
    for (const token of ["--color-fg", "--color-mist-300", "--color-fg-muted", "--color-fg-subtle", "--color-link", "--color-danger"]) {
      expect(contrast(tokenHex(token), high), `${token} on ${high}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("white text on every solid fill clears 4.5:1", () => {
    for (const fill of ["--color-accent", "--color-accent-hover", "--color-danger-solid", "--color-danger-solid-hover"]) {
      expect(contrast("#ffffff", tokenHex(fill)), `white on ${fill}`).toBeGreaterThanOrEqual(4.5);
    }
    expect(tokenHex("--color-accent-fg").toLowerCase()).toBe("#ffffff");
    expect(tokenHex("--color-danger-fg").toLowerCase()).toBe("#ffffff");
  });

  it("the hover fills are darker than their resting fills, so white only gains contrast on hover", () => {
    expect(relativeLuminance(tokenHex("--color-accent-hover"))).toBeLessThan(relativeLuminance(tokenHex("--color-accent")));
    expect(relativeLuminance(tokenHex("--color-danger-solid-hover"))).toBeLessThan(relativeLuminance(tokenHex("--color-danger-solid")));
  });

  it("the edge of a control clears the 3:1 non-text floor (WCAG 1.4.11)", () => {
    // The old `--border` measured 1.97:1 and the old file recorded that as a
    // known failure. Input and outlined-button borders use `control` now.
    for (const surface of SURFACES.slice(0, 3)) {
      expect(contrast(tokenHex("--color-control"), surface), `control on ${surface}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("the accent fill is distinguishable from the page it sits on (3:1)", () => {
    expect(contrast(tokenHex("--color-accent"), APP_BACKGROUND)).toBeGreaterThanOrEqual(3);
  });

  it("declares every token this file references", () => {
    // Guards against a utility referencing a token that was renamed.
    const declared = new Set(declaredTokens());
    const referenced = new Set(
      [...css.matchAll(/var\(\s*(--[\w-]+)\s*[,)]/g)].map((m) => m[1] as string),
    );
    for (const name of referenced) {
      expect(declared, `${name} is referenced but never declared`).toContain(name);
    }
  });
});
