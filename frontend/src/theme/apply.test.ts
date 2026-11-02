// A theme on the page: its custom properties (classic's today's look exactly, every one the
// stylesheet reads, studio's the stylesheet's own defaults), `data-theme`, and its fonts loaded
// before `applyTheme` resolves, or failed without a rejection.
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { applyTheme, themeProperties } from "./apply";
import { THEMES } from "./presets";
import { classic } from "./presets/classic";
import { studio } from "./presets/studio";
import type { Theme } from "./theme";

const CSS = readFileSync(new URL("../style.css", import.meta.url), "utf8");

const DARK: Theme = {
  ...studio,
  scheme: "dark",
  colors: { ...studio.colors, panel: "#0b1020" },
  shape: { ...studio.shape, radius: "0", uppercase: true },
};

describe("themeProperties", () => {
  it("gives classic the stylesheet's first values", () => {
    expect(themeProperties(classic)).toMatchObject({
      "color-scheme": "light",
      "--panel": "#f7f7f5",
      "--ink": "#1f2328",
      "--hover": "#eef4fa",
      "--field": "#ffffff",
      "--link": "#2c7fb8",
      "--accent-ink": "#ffffff",
      "--map-bg": "#ffffff",
      "--route-casing": "#ffffff",
      "--ghost": "rgb(255 255 255 / 70%)",
      "--attribution": "rgb(255 255 255 / 50%)",
      "--attribution-ink": "rgb(31 35 40 / 75%)",
      "--draw": "#7c3aed",
      "--radius": "6px",
      "--marker-radius": "50%",
      "--brand-case": "none",
      "--font-body": 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    });
  });

  it("links in the accent unless a theme says, buttons on the panel when dark, 0 as a length", () => {
    expect(themeProperties(studio)["--link"]).toBe("#ff5a1f");
    expect(themeProperties(studio)["--field"]).toBe("#ffffff");
    const dark = themeProperties(DARK);
    expect(dark["color-scheme"]).toBe("dark");
    expect(dark["--field"]).toBe("#0b1020");
    expect(dark["--radius"]).toBe("0px");
    expect(dark["--brand-case"]).toBe("uppercase");
    expect(dark["--attribution"]).toBe("rgb(11 16 32 / 50%)");
    const short = { ...studio, colors: { ...studio.colors, routeCasing: "#1a1" } };
    expect(themeProperties(short)["--ghost"]).toBe("rgb(17 170 17 / 70%)");
  });

  it("is studio's in the stylesheet's own :root, so the page is styled before the script runs", () => {
    const root = CSS.slice(CSS.indexOf(":root {"), CSS.indexOf("}", CSS.indexOf(":root {")));
    const declared = Object.fromEntries(
      [...root.matchAll(/^\s*((?:--)?[a-z-]+):\s*([^;]+);/gm)].map((m) => [m[1], m[2].trim()]),
    );
    expect(declared).toMatchObject(themeProperties(studio));
  });

  it("writes every variable the stylesheet reads, for every theme", () => {
    const read = new Set([...CSS.matchAll(/var\((--[a-z-]+)/g)].map((match) => match[1]));
    expect(read.size).toBeGreaterThan(20);
    for (const theme of THEMES) {
      const written = Object.keys(themeProperties(theme));
      expect([...read].filter((name) => !written.includes(name))).toEqual([]);
    }
  });
});

describe("applyTheme", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  /** A stand-in <html>: the properties set on it, and the fonts asked of its document. */
  function stubRoot(failing = false) {
    const properties = new Map<string, string>();
    const asked: string[] = [];
    const root = {
      dataset: {} as Record<string, string>,
      style: { setProperty: (name: string, value: string) => properties.set(name, value) },
      ownerDocument: {
        fonts: {
          load: async (font: string) => {
            asked.push(font);
            if (failing) throw new Error("NetworkError");
            return [];
          },
        },
      },
    };
    return { root, properties, asked };
  }

  it("puts the theme on the root, then loads its fonts", async () => {
    const { root, properties, asked } = stubRoot();
    const steps: string[] = [];
    const theme: Theme = {
      ...studio,
      fonts: { ...studio.fonts, load: async () => void steps.push(`load ${properties.size}`) },
    };
    await applyTheme(theme, root as unknown as HTMLElement);
    expect(root.dataset.theme).toBe("studio");
    expect(Object.fromEntries(properties)).toEqual(themeProperties(studio));
    expect(steps).toEqual([`load ${properties.size}`]); // once every property is set
    expect(asked).toEqual([
      `700 1em ${studio.fonts.display}`,
      `1em ${studio.fonts.body}`,
      `800 1em ${studio.fonts.numbers}`,
    ]);
  });

  it("resolves when the fonts fail, said once at debug level only", async () => {
    const said = vi.spyOn(console, "debug").mockImplementation(() => {});
    const shouted = vi.spyOn(console, "error").mockImplementation(() => {});
    const offline: Theme = {
      ...studio,
      fonts: { ...studio.fonts, load: () => Promise.reject(new TypeError("Failed to fetch")) },
    };
    const first = stubRoot();
    await applyTheme(offline, first.root as unknown as HTMLElement);
    expect(Object.fromEntries(first.properties)).toEqual(themeProperties(studio));
    expect(first.asked).toEqual([]);
    const second = stubRoot(true);
    await applyTheme(studio, second.root as unknown as HTMLElement);
    expect(said).toHaveBeenCalledTimes(2); // one line for each theme put on
    expect(String(said.mock.calls[0][0])).toContain("Failed to fetch");
    expect(shouted).not.toHaveBeenCalled();
  });
});
