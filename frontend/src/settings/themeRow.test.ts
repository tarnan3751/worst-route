import { describe, expect, it } from "vitest";
import { THEMES } from "../theme/presets";
import { AUTO_LABEL, themeOptions } from "./themeRow";

describe("the theme picker", () => {
  it("offers auto first, then every theme by its name, in their order", () => {
    const options = themeOptions(THEMES);
    expect(options[0]).toEqual(["auto", AUTO_LABEL]);
    expect(options.slice(1)).toEqual(THEMES.map(({ id, name }) => [id, name]));
    expect(options.map(([id]) => id)).toContain("classic");
  });
});
