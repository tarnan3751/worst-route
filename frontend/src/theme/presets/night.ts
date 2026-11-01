// Night drive (docs/reference/frontend.md, **Themes**): a dark map at 2 a.m., deep navy, streets
// barely there; a mood, not an instrument panel. Its one bold thing is the glow: the route is
// drawn twice, a wide, translucent, blurred magenta casing under a crisp magenta line (`glow`, the
// casing 9 wide), so the worst thing on screen is also the brightest. The two colours split the
// roles: cyan for the sensible route, the start and a drawing's next moves, magenta for
// everything the engine did. The markers' digits are the panel's navy, dark on all three, in a
// ring of the map's. Sora throughout, regular for the body and bold for the name and the ratio.
import type { Theme } from "../theme";

export const night: Theme = {
  id: "night",
  name: "Night drive",
  scheme: "dark",
  basemap: "dark",
  colors: {
    panel: "#0b1020",
    ink: "#e6e9ff",
    muted: "#8b93b8",
    line: "#1c2340",
    hover: "#1a1327", // 6 % of the accent over the panel
    accent: "#ff3d9a",
    accentInk: "#ffffff",
    mapBg: "#070b16",
    start: "#35e0ff",
    via: "#8b93b8",
    end: "#ff3d9a",
    markerInk: "#0b1020",
    route: ["#ff3d9a"],
    routeCasing: "#070b16", // the markers' rings and a drawing's casing; the glow is the route's
    baseline: "#35e0ff",
    flash: "#35e0ff",
    highlight: "#35e0ff",
    draw: "#ff3d9a",
    par: "#35e0ff",
    toast: "#070b16",
    toastInk: "#35e0ff",
    errorBg: "#3a1518",
    errorInk: "#ffb4b4",
  },
  fonts: {
    display: '"Sora", system-ui, sans-serif',
    body: '"Sora", system-ui, sans-serif',
    numbers: '"Sora", system-ui, sans-serif',
    load: () =>
      Promise.all([import("@fontsource/sora/400.css"), import("@fontsource/sora/700.css")]),
  },
  shape: {
    radius: "8px",
    markerRadius: "50%",
    routeWidth: 4,
    casingWidth: 9,
    uppercase: false,
    glow: true,
  },
};
