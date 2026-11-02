// Studio: a current consumer app, done well. White surfaces on a cool grey map (Protomaps'
// grayscale flavor), ink and grey everywhere but one wrong-way orange, which is the route (a flat
// line), Go and the ratio; teal for the start, and for what must read against the orange route: a
// splice's flash and a step of the directions. Manrope, heavy for the name and the ratio, medium
// for the rest. Rounded where a hand touches it.
import type { Theme } from "../theme";

const MANROPE = 'Manrope, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export const studio: Theme = {
  id: "studio",
  name: "Studio",
  scheme: "light",
  basemap: "grayscale",
  colors: {
    panel: "#ffffff",
    ink: "#101418",
    muted: "#677383",
    line: "#e5e8ec",
    hover: "#fff5f2", // the accent at 6 % over the panel
    accent: "#ff5a1f",
    accentInk: "#ffffff",
    mapBg: "#f2f4f7",
    start: "#0ea5a4",
    via: "#677383",
    end: "#ff5a1f",
    markerInk: "#ffffff",
    route: ["#ff5a1f"],
    routeCasing: "#ffffff",
    baseline: "#7c8796",
    flash: "#0ea5a4",
    highlight: "#0ea5a4",
    draw: "#ff5a1f",
    par: "#7c8796",
    toast: "#fff1eb",
    toastInk: "#9a3412",
    errorBg: "#fde8e8",
    errorInk: "#8a1c1c",
  },
  fonts: {
    display: MANROPE,
    body: MANROPE,
    numbers: MANROPE,
    load: () =>
      Promise.all([import("@fontsource/manrope/500.css"), import("@fontsource/manrope/800.css")]),
  },
  shape: {
    radius: "10px",
    markerRadius: "50%",
    routeWidth: 4,
    casingWidth: 7,
    uppercase: false,
    glow: false,
  },
};
