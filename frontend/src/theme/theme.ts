// A theme (docs/reference/frontend.md, **Themes**): every colour, font and shape the app draws
// with, the route's colours and widths on the map, and the variant of the basemap's tiles. A
// preset is one `Theme` under presets/, data only; theme/apply.ts writes it on the page as CSS
// custom properties, and the map's layers take theirs from it (map/routeLayer.ts `paintRoute`,
// map/drawLayer.ts `paintDrawing`, map/basemap.ts `Basemap`).

export type ThemeId =
  | "classic"
  | "studio"
  | "night"
  | "transit"
  | "topo"
  | "arcade"
  | "dashboard"
  | "atlas"
  | "sign"
  | "ledger";
export type Scheme = "light" | "dark";
/** The basemap's tiles: Protomaps' grayscale flavor (classic's; OpenFreeMap's Positron, as light),
 * its light or its dark flavor (OpenFreeMap's Positron or Dark). */
export type BasemapVariant = "grayscale" | Scheme;

export interface ThemeColors {
  panel: string; // the panel and its text
  ink: string;
  muted: string;
  line: string;
  hover: string;
  accent: string; // Go, the big ratio, links
  accentInk: string;
  /** Links, the challenge set up and the focus ring; the accent when left out (classic's are
   * today's blue, its via colour). */
  link?: string;
  mapBg: string; // behind the tiles while they load
  start: string; // the stop markers
  via: string;
  end: string;
  markerInk: string;
  route: string[]; // 1 to 4 colours, the route's gradient from start to end
  routeCasing: string; // the casing under the route and the drawing, the ring of a marker
  baseline: string; // the sensible route
  flash: string; // a splice's flash
  highlight: string; // the directions' step
  draw: string; // the drawing mode: your line
  par: string; // the drawing mode: the next moves
  toast: string;
  toastInk: string;
  errorBg: string;
  errorInk: string;
}

export interface ThemeFonts {
  display: string; // CSS font-family stacks, with fallbacks
  body: string;
  numbers: string;
  /** Dynamic imports of the fontsource CSS; resolves when loaded (classic's at once). */
  load: () => Promise<unknown>;
}

export interface ThemeShape {
  radius: string; // CSS lengths; "50%" round markers, "3px" chips, "0" squares
  markerRadius: string;
  routeWidth: number; // MapLibre line widths at the route's zoom (4 and 7 are today's)
  casingWidth: number;
  uppercase: boolean; // the brand and Go in capitals (the section headings always are)
  glow: boolean; // the casing drawn wide, translucent and blurred, in the route's colour
}

export interface Theme {
  id: ThemeId;
  name: string;
  scheme: Scheme;
  basemap: BasemapVariant;
  colors: ThemeColors;
  fonts: ThemeFonts;
  shape: ThemeShape;
}
