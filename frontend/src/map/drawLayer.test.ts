// The drawing's paint: its colours from the theme, as the route's are (routeLayer.test.ts).
import type { Map as MapLibreMap } from "maplibre-gl";
import { describe, expect, it, vi } from "vitest";
import { classic } from "../theme/presets/classic";
import { studio } from "../theme/presets/studio";
import type { Theme } from "../theme/theme";
import { paintDrawing } from "./drawLayer";

// The module makes a popup for hovered moves; only its type is MapLibre's here.
vi.mock("maplibre-gl", () => ({ Popup: class {} }));

function painted(theme: Theme): Record<string, unknown> {
  const paint: Record<string, unknown> = {};
  const map = {
    setPaintProperty: (id: string, name: string, value: unknown) => {
      paint[`${id} ${name}`] = value;
    },
  } as unknown as MapLibreMap;
  paintDrawing(map, theme);
  return paint;
}

describe("the drawing's paint", () => {
  it("is the first look's under classic: violet over white, the next moves green", () => {
    expect(painted(classic)).toEqual({
      "wr-drawn-casing line-color": "#ffffff",
      "wr-drawn line-color": "#7c3aed",
      "wr-choices line-color": "#059669",
      "wr-choices-ends circle-color": "#059669",
      "wr-choices-ends circle-stroke-color": "#ffffff",
      "wr-at circle-color": "#7c3aed",
      "wr-at circle-stroke-color": "#ffffff",
    });
  });

  it("takes another theme's draw, par and casing colours", () => {
    const night = { ...studio, colors: { ...studio.colors, routeCasing: "#070b16" } };
    expect(painted(night)).toEqual({
      "wr-drawn-casing line-color": "#070b16",
      "wr-drawn line-color": "#ff5a1f",
      "wr-choices line-color": "#7c8796",
      "wr-choices-ends circle-color": "#7c8796",
      "wr-choices-ends circle-stroke-color": "#070b16",
      "wr-at circle-color": "#ff5a1f",
      "wr-at circle-stroke-color": "#070b16",
    });
  });
});
