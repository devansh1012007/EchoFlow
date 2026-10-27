import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Source-level guards for the design system in `styles/tokens.css`.
 *
 * These read the component source as text. They prove no off-palette colour is
 * *written* in a class string; they cannot prove what a browser paints. The
 * built stylesheet is checked separately (`vite build` + grep) because Tailwind
 * is not processed under vitest.
 */

const SRC = resolve(process.cwd(), "src");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return name === "test" ? [] : sourceFiles(full);
    return /\.(tsx|ts)$/.test(name) ? [full] : [];
  });
}

/** Source with `//` and block comments blanked, so prose about old colours does not trip a guard. */
function code(file: string): string {
  return readFileSync(file, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

const files = sourceFiles(SRC);

describe("design system — no off-palette colour in source", () => {
  it("finds source files to scan", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  it("has no arbitrary hex colour in a class string", () => {
    const offenders = files.flatMap((f) =>
      [...code(f).matchAll(/\[#[0-9a-fA-F]{3,8}\]/g)].map((m) => `${relative(SRC, f)}: ${m[0]}`),
    );
    expect(offenders).toEqual([]);
  });

  it("has no Tailwind default-palette colour class (red-500, green-400, ...)", () => {
    const rx =
      /\b(?:bg|text|border|ring|from|to|via|fill|stroke|divide|shadow|outline|accent|caret|decoration)-(?:red|blue|green|emerald|amber|yellow|orange|purple|violet|pink|rose|sky|indigo|teal|cyan|lime|fuchsia|zinc|gray|slate|neutral|stone)-\d{2,3}/g;
    const offenders = files.flatMap((f) =>
      [...code(f).matchAll(rx)].map((m) => `${relative(SRC, f)}: ${m[0]}`),
    );
    expect(offenders).toEqual([]);
  });

  it("never sets black text on the blue action fill", () => {
    // Black on #2459e0 is 3.58:1. White on it is 5.87:1.
    const offenders = files.flatMap((f) =>
      [...code(f).matchAll(/"[^"\n]*"|`[^`]*`/g)]
        .map((m) => m[0])
        .filter((s) => /(^|[\s"`:])bg-accent(?=[\s"`/])/.test(s) && /(^|[\s"`:])text-black(?=[\s"`])/.test(s))
        .map((s) => `${relative(SRC, f)}: ${s.slice(0, 80)}`),
    );
    expect(offenders).toEqual([]);
  });

  it("uses no gradient utilities", () => {
    const offenders = files.flatMap((f) =>
      [...code(f).matchAll(/\b(?:bg-gradient-to-\w+|bg-linear-\S+|bg-radial\S*|bg-conic\S*)/g)].map(
        (m) => `${relative(SRC, f)}: ${m[0]}`,
      ),
    );
    expect(offenders).toEqual([]);
  });
});
