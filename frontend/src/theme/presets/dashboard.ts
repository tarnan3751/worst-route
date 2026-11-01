// Dashboard (docs/reference/frontend.md, **Themes**): the sat-nav that has quietly given up, a
// dark instrument panel. Its one bold thing is the readout: the ratio amber on near-black in a
// monospace, and the route the same amber on the dark map, the only warm thing on screen; the
// markers are square chips with the stop number, dark on all three. IBM Plex Mono (500, 700) for
// the name and every number, IBM Plex Sans (400, 600) for the rest. Capitals are meant for the
// brand alone here, as an instrument's label; `uppercase` capitalises Go as well, which the
// interface does not tell apart.
import type { Theme } from "../theme";

export const dashboard: Theme = {
  id: "dashboard",
  name: "Dashboard",
  scheme: "dark",
  basemap: "dark",
  colors: {
    panel: "#141a22",
    ink: "#e9eef4",
    muted: "#8d9aaa",
    line: "#2a333f",
    hover: "#222222", // 6 % of the accent over the panel
    accent: "#f5a524",
    accentInk: "#141a22",
    mapBg: "#0e1319",
    start: "#4cc2ff",
    via: "#8d9aaa",
    end: "#ff5c5c",
    markerInk: "#141a22",
    route: ["#f5a524"],
    routeCasing: "#0e1319",
    baseline: "#5c6b7c",
    flash: "#4cc2ff",
    highlight: "#4cc2ff",
    draw: "#f5a524",
    par: "#5c6b7c",
    toast: "#0b0f14",
    toastInk: "#f5a524",
    errorBg: "#3a1518",
    errorInk: "#ffb4b4",
  },
  fonts: {
    display: '"IBM Plex Mono", ui-monospace, monospace',
    body: '"IBM Plex Sans", system-ui, sans-serif',
    numbers: '"IBM Plex Mono", ui-monospace, monospace',
    load: () =>
      Promise.all([
        import("@fontsource/ibm-plex-mono/500.css"),
        import("@fontsource/ibm-plex-mono/700.css"),
        import("@fontsource/ibm-plex-sans/400.css"),
        import("@fontsource/ibm-plex-sans/600.css"),
      ]),
  },
  shape: {
    radius: "6px",
    markerRadius: "3px",
    routeWidth: 4,
    casingWidth: 7,
    uppercase: true,
    glow: false,
  },
};
