// Classic: the app's first look, kept exactly. A warm grey panel in the system's fonts, red Go
// and ratio, green, blue and red stop markers, and the route a gradient from yellow at the start
// to dark red at the end over a white casing, on Protomaps' grayscale flavor (OpenFreeMap's
// Positron). Links, the challenge set up and the hovers are blue, its via colour; behind the tiles
// is the page's white.
import type { Theme } from "../theme";

const SYSTEM = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export const classic: Theme = {
  id: "classic",
  name: "Classic",
  scheme: "light",
  basemap: "grayscale",
  colors: {
    panel: "#f7f7f5",
    ink: "#1f2328",
    muted: "#6b7280",
    line: "#d6d9de",
    hover: "#eef4fa",
    accent: "#d7191c",
    accentInk: "#ffffff",
    link: "#2c7fb8",
    mapBg: "#ffffff",
    outside: "#e2e2df", // outside the US: a plain grey a step lighter than the grayscale land
    start: "#1a9850",
    via: "#2c7fb8",
    end: "#d7191c",
    markerInk: "#ffffff",
    route: ["#fdd049", "#f98e2b", "#e8452c", "#b10026"],
    routeCasing: "#ffffff",
    baseline: "#4b5563",
    flash: "#00e5ff",
    highlight: "#2563eb",
    draw: "#7c3aed",
    par: "#059669",
    toast: "#f7f7f5",
    toastInk: "#1f2328",
    errorBg: "#fde8e8",
    errorInk: "#8a1c1c",
  },
  fonts: { display: SYSTEM, body: SYSTEM, numbers: SYSTEM, load: () => Promise.resolve() },
  shape: {
    radius: "6px",
    markerRadius: "50%",
    routeWidth: 4,
    casingWidth: 7,
    uppercase: false,
    glow: false,
  },
};
