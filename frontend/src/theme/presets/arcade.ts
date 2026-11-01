// Arcade (docs/reference/frontend.md, **Themes**): a 1990s in-car navigation unit, a navy screen,
// a yellow route, cyan text, pixel type; the joke app that looks like a joke. Its one bold thing
// is the square corners: no radius anywhere, square markers, the route a yellow line over the
// screen's navy, and the brand, the section headings and Go in capitals. Press Start 2P for the
// name and the numbers, VT323 for the rest (one weight each, 400).
import type { Theme } from "../theme";

export const arcade: Theme = {
  id: "arcade",
  name: "Arcade",
  scheme: "dark",
  basemap: "dark",
  colors: {
    panel: "#0a1e5e",
    ink: "#ffffff",
    muted: "#9fb3ff",
    line: "#1d3a9a",
    hover: "#192a5d", // 6 % of the accent over the panel
    accent: "#ffe14d",
    accentInk: "#0a1e5e",
    mapBg: "#061346",
    start: "#7fe7ff",
    via: "#9fb3ff",
    end: "#ff5e5e",
    markerInk: "#061346",
    route: ["#ffe14d"],
    routeCasing: "#061346",
    baseline: "#7fe7ff",
    flash: "#7fe7ff",
    highlight: "#7fe7ff",
    draw: "#ffe14d",
    par: "#7fe7ff",
    toast: "#000000",
    toastInk: "#ffe14d",
    errorBg: "#3a1518",
    errorInk: "#ffb4b4",
  },
  fonts: {
    display: '"Press Start 2P", monospace',
    body: '"VT323", monospace',
    numbers: '"Press Start 2P", monospace',
    load: () =>
      Promise.all([
        import("@fontsource/press-start-2p/400.css"),
        import("@fontsource/vt323/400.css"),
      ]),
  },
  shape: {
    radius: "0",
    markerRadius: "0",
    routeWidth: 4,
    casingWidth: 7,
    uppercase: true,
    glow: false,
  },
};
