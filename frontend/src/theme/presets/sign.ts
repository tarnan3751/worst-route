// Highway sign (docs/reference/frontend.md, **Themes**): the app as a run of US road signs,
// interstate green, warning yellow, white type; the loudest of the looks. The panel is a green
// sign. Its one bold thing is the yellow diversion over black casing: the route warning yellow
// over a black casing, the colour of a road-work detour, and the toast strip a black board with
// yellow type. Capitals on the signs (the brand, the headings, Go), sentence case underneath.
// Overpass (500, 700, 800), a free face inspired by Highway Gothic, for everything. The one ink
// colour is dark for the yellow start and the white via; the red end's digit is dark too. The
// flash and the directions' highlight are the stop red, not the start's yellow, which is the
// route's, nor the casing's black.
import type { Theme } from "../theme";

export const sign: Theme = {
  id: "sign",
  name: "Highway sign",
  scheme: "light",
  basemap: "light",
  colors: {
    panel: "#0f6b4a",
    ink: "#ffffff",
    muted: "#bfe3d1",
    line: "#0b5339",
    hover: "#1d7046", // 6 % of the accent over the panel
    accent: "#f7c600",
    accentInk: "#1a1a1a",
    mapBg: "#e9ecef",
    outside: "#c9ced3", // outside the US: a cool grey a step darker than the light land
    start: "#f7c600",
    via: "#ffffff",
    end: "#c8102e",
    markerInk: "#1a1a1a",
    route: ["#f7c600"],
    routeCasing: "#1a1a1a",
    baseline: "#6c757d",
    flash: "#c8102e",
    highlight: "#c8102e",
    draw: "#f7c600",
    par: "#6c757d",
    toast: "#1a1a1a",
    toastInk: "#f7c600",
    errorBg: "#fde8e8",
    errorInk: "#8a1c1c",
  },
  fonts: {
    display: '"Overpass", system-ui, sans-serif',
    body: '"Overpass", system-ui, sans-serif',
    numbers: '"Overpass", system-ui, sans-serif',
    load: () =>
      Promise.all([
        import("@fontsource/overpass/500.css"),
        import("@fontsource/overpass/700.css"),
        import("@fontsource/overpass/800.css"),
      ]),
  },
  shape: {
    radius: "4px",
    markerRadius: "3px",
    routeWidth: 4,
    casingWidth: 7,
    uppercase: true,
    glow: false,
  },
};
