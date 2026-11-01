// Transit (docs/reference/frontend.md, **Themes**): Swiss wayfinding, black on white, a grotesk set
// tight, hairline rules. Its one bold thing is the metro line: the route a signal-orange line with
// no casing (the casing 0), station circles at the stops (white, the end signal orange, black
// digits and black rings on all three), and the sensible route a thin black line, as if the map
// were a network diagram with one absurd service on it. The flash and the directions' highlight are
// black, not the start's white, which vanished into the light map; a line drawn by hand is black
// too and the moves it may take next grey. Archivo throughout, regular for the body and extra-bold
// (800) for the name and the ratio.
import type { Theme } from "../theme";

export const transit: Theme = {
  id: "transit",
  name: "Transit",
  scheme: "light",
  basemap: "light",
  colors: {
    panel: "#ffffff",
    ink: "#000000",
    muted: "#5a5a5a",
    line: "#000000",
    hover: "#f0f0f0", // 6 % of the accent over the panel
    accent: "#000000",
    accentInk: "#ffffff",
    mapBg: "#f0f0f0",
    start: "#ffffff",
    via: "#ffffff",
    end: "#f26b1d",
    markerInk: "#000000",
    route: ["#f26b1d"],
    routeCasing: "#000000", // the markers' rings and a drawing's casing; the route has none
    baseline: "#000000",
    flash: "#000000",
    highlight: "#000000",
    draw: "#000000",
    par: "#5a5a5a",
    toast: "#000000",
    toastInk: "#ffffff",
    errorBg: "#fde8e8",
    errorInk: "#8a1c1c",
  },
  fonts: {
    display: '"Archivo", "Helvetica Neue", Arial, sans-serif',
    body: '"Archivo", "Helvetica Neue", Arial, sans-serif',
    numbers: '"Archivo", "Helvetica Neue", Arial, sans-serif',
    load: () =>
      Promise.all([
        import("@fontsource/archivo/400.css"),
        import("@fontsource/archivo/600.css"),
        import("@fontsource/archivo/800.css"),
      ]),
  },
  shape: {
    radius: "0",
    markerRadius: "50%",
    routeWidth: 4,
    casingWidth: 0,
    uppercase: false,
    glow: false,
  },
};
