// Guards against faint text coming back. On the app's near-black background the grey text-slate-500
// (and darker) works out to about 4:1 contrast, below the 4.5:1 that small text needs to stay
// readable. Informational text must use text-slate-400 (about 7:1) or lighter.
import { describe, expect, it } from "vitest";

// Every source file except the tests, read as text at build time (a Vite feature, so no Node
// typings are needed).
const sources = import.meta.glob(["/src/**/*.{ts,tsx}", "!/src/**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

// The only two uses that are allowed: large text (3:1 is enough from 24px) and a decorative icon.
const ALLOWED = [
  "text-2xl font-light tracking-tight text-slate-500 sm:text-3xl", // the hero's second line
  "<CircleIcon className={`${base} text-slate-500`} />", // the empty circle of a waiting step
];

describe("faint text", () => {
  it("scans the real source files", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(20);
    expect(Object.keys(sources).some((file) => file.endsWith("/App.tsx"))).toBe(true);
    expect(Object.keys(sources).some((file) => file.endsWith(".test.tsx"))).toBe(false);
  });

  it("is not used for anything that has to be read", () => {
    const offenders: string[] = [];
    for (const [file, original] of Object.entries(sources)) {
      let text = original;
      for (const allowed of ALLOWED) text = text.split(allowed).join("");
      for (const match of text.matchAll(/text-slate-(500|600|700|800|900)\b/g)) {
        offenders.push(`${file}: ${match[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
