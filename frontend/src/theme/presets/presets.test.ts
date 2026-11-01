// The presets (docs/reference/frontend.md, **Themes**): THEMES in the order the settings list
// them, each id once; every colour a hex colour and the route one to four of them; and each
// preset's fonts loading, the fontsource stylesheets its `load` imports resolving.
import { describe, expect, it } from "vitest";
import { THEMES } from "./index";

const ORDER = [
  "studio",
  "night",
  "transit",
  "topo",
  "arcade",
  "dashboard",
  "atlas",
  "sign",
  "ledger",
  "classic",
];

describe("the theme presets", () => {
  it("are listed in the settings' order, each once", () => {
    expect(THEMES.map(({ id }) => id)).toEqual(ORDER);
  });

  it("give their colours in hex, the route one to four of them", () => {
    for (const { id, colors } of THEMES) {
      expect(colors.route.length, id).toBeGreaterThanOrEqual(1);
      expect(colors.route.length, id).toBeLessThanOrEqual(4);
      for (const [name, value] of Object.entries(colors)) {
        for (const colour of [value].flat()) {
          expect(colour, `${id}.${name}`).toMatch(/^#([0-9a-f]{3}|[0-9a-f]{6})$/);
        }
      }
    }
  });

  it("load their fonts", async () => {
    for (const { fonts } of THEMES) await fonts.load();
  });
});
