// Road atlas (docs/reference/frontend.md, **Themes**): a page from a paper road atlas, light and
// calm, the most "map app" of the looks. Its one bold thing is the cased blue highway: the route
// drawn the way an atlas draws one, solid atlas blue over a white casing, the sensible route a
// thin grey dash beside it, highway red only for Go and the end. Section headings in small
// condensed capitals, like an index page. Barlow Condensed (600, 700) for the name, the headings
// and the numbers, Source Sans 3 (400, 600) for the body.
import type { Theme } from "../theme";

export const atlas: Theme = {
  id: "atlas",
  name: "Road atlas",
  scheme: "light",
  basemap: "light",
  colors: {
    panel: "#fbfaf6",
    ink: "#15202b",
    muted: "#5f6b78",
    line: "#d8d6cd",
    hover: "#f8ecea", // 6 % of the accent over the panel
    accent: "#c8102e",
    accentInk: "#ffffff",
    mapBg: "#f3f1ea",
    start: "#006b3f",
    via: "#1b4f9c",
    end: "#c8102e",
    markerInk: "#ffffff",
    route: ["#1b4f9c"],
    routeCasing: "#ffffff",
    baseline: "#6b7480",
    flash: "#006b3f",
    highlight: "#006b3f",
    draw: "#c8102e",
    par: "#6b7480",
    toast: "#1b4f9c",
    toastInk: "#ffffff",
    errorBg: "#fde8e8",
    errorInk: "#8a1c1c",
  },
  fonts: {
    display: '"Barlow Condensed", "Arial Narrow", sans-serif',
    body: '"Source Sans 3", system-ui, sans-serif',
    numbers: '"Barlow Condensed", "Arial Narrow", sans-serif',
    load: () =>
      Promise.all([
        import("@fontsource/barlow-condensed/600.css"),
        import("@fontsource/barlow-condensed/700.css"),
        import("@fontsource/source-sans-3/400.css"),
        import("@fontsource/source-sans-3/600.css"),
      ]),
  },
  shape: {
    radius: "2px",
    markerRadius: "50%",
    routeWidth: 4,
    casingWidth: 7,
    uppercase: true,
    glow: false,
  },
};
