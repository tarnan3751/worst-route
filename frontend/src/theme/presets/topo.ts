// Topo (docs/reference/frontend.md, **Themes**): an outdoors app's language, a cool paper ground,
// pine green for the chrome (Go, the headings, the toast strip), trail orange for the route and
// the end, a slab for the headings; the friendliest of the looks. Its one bold thing, contour
// lines under the streets from the basemap's terrain, comes later, with a basemap that has them;
// until then it is the palette and the type. Zilla Slab (600, 700) for the name, the headings and
// the numbers, Nunito Sans (400, 700) for the body.
import type { Theme } from "../theme";

export const topo: Theme = {
  id: "topo",
  name: "Topo",
  scheme: "light",
  basemap: "light",
  colors: {
    panel: "#eef0ea",
    ink: "#1f2a1f",
    muted: "#5c6b5c",
    line: "#d3d8cc",
    hover: "#e3e7df", // 6 % of the accent over the panel
    accent: "#2f5d3a",
    accentInk: "#ffffff",
    mapBg: "#e7ebe0",
    start: "#2f5d3a",
    via: "#5c6b5c",
    end: "#e07a1f",
    markerInk: "#ffffff",
    route: ["#e07a1f"],
    routeCasing: "#ffffff",
    baseline: "#6c7a6c",
    flash: "#2f5d3a",
    highlight: "#2f5d3a",
    draw: "#2f5d3a",
    par: "#6c7a6c",
    toast: "#2f5d3a",
    toastInk: "#eef0ea",
    errorBg: "#fde8e8",
    errorInk: "#8a1c1c",
  },
  fonts: {
    display: '"Zilla Slab", Georgia, serif',
    body: '"Nunito Sans", system-ui, sans-serif',
    numbers: '"Zilla Slab", Georgia, serif',
    load: () =>
      Promise.all([
        import("@fontsource/zilla-slab/600.css"),
        import("@fontsource/zilla-slab/700.css"),
        import("@fontsource/nunito-sans/400.css"),
        import("@fontsource/nunito-sans/700.css"),
      ]),
  },
  shape: {
    radius: "6px",
    markerRadius: "50%",
    routeWidth: 4,
    casingWidth: 7,
    uppercase: false,
    glow: false,
  },
};
