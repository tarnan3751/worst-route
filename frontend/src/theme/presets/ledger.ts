// Ledger (docs/reference/frontend.md, **Themes**): the trip as an itemised bill of waste, black
// type on white, a typewriter face, quiet and bureaucratic. Its one bold thing is the single red
// line: the route stamp red, the only red line on an otherwise grey map (and the end marker the
// only red stop); everything else ink and grey, square markers, no rounded corners anywhere.
// Courier Prime (400, 700) for labels, numbers and body alike.
import type { Theme } from "../theme";

export const ledger: Theme = {
  id: "ledger",
  name: "Ledger",
  scheme: "light",
  basemap: "light",
  colors: {
    panel: "#ffffff",
    ink: "#111111",
    muted: "#666666",
    line: "#111111",
    hover: "#f1f1f1", // 6 % of the accent over the panel
    accent: "#111111",
    accentInk: "#ffffff",
    mapBg: "#f4f4f2",
    start: "#111111",
    via: "#666666",
    end: "#b3261e",
    markerInk: "#ffffff",
    route: ["#b3261e"],
    routeCasing: "#ffffff",
    baseline: "#777777",
    flash: "#111111",
    highlight: "#111111",
    draw: "#111111",
    par: "#777777",
    toast: "#ffffff",
    toastInk: "#111111",
    errorBg: "#fde8e8",
    errorInk: "#8a1c1c",
  },
  fonts: {
    display: '"Courier Prime", "Courier New", monospace',
    body: '"Courier Prime", "Courier New", monospace',
    numbers: '"Courier Prime", "Courier New", monospace',
    load: () =>
      Promise.all([
        import("@fontsource/courier-prime/400.css"),
        import("@fontsource/courier-prime/700.css"),
      ]),
  },
  shape: {
    radius: "0",
    markerRadius: "0",
    routeWidth: 4,
    casingWidth: 7,
    uppercase: false,
    glow: false,
  },
};
