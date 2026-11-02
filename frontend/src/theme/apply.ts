// A theme on the page (docs/reference/frontend.md, **Themes**): `data-theme` on <html>, and every
// colour, font and shape as a CSS custom property on it (`themeProperties`), which style.css
// styles through and nothing else: `color-scheme` (native controls and scrollbars follow it),
// `--field`, the buttons' background (white on a light scheme, the panel on a dark one), the
// translucent colours (`--ghost`, `--attribution`, `--attribution-ink`: rgb() with an alpha, which
// every web view takes, where color-mix() is too new for an older macOS's), and `--brand-case`,
// the brand's and Go's capitals, among them. The theme's fonts are loaded too, at best: `applyTheme`
// resolves once they are in, or once they have failed (offline, a font file replaced), said only
// on the console's debug level, the page then in the stacks' fallbacks.
import type { Theme } from "./theme";

/** `hex` (#rgb or #rrggbb) at `alpha` (0 to 1), as rgb() with an alpha. */
function translucent(hex: string, alpha: number): string {
  const digits = hex.length === 4 ? [...hex.slice(1)].map((d) => d + d).join("") : hex.slice(1);
  const [r, g, b] = [0, 2, 4].map((k) => parseInt(digits.slice(k, k + 2), 16));
  return `rgb(${r} ${g} ${b} / ${Math.round(alpha * 100)}%)`;
}

/** The CSS custom properties (and `color-scheme`) that put `theme` on the page. */
export function themeProperties(theme: Theme): Record<string, string> {
  const { colors, fonts, shape } = theme;
  const field = theme.scheme === "dark" ? colors.panel : "#ffffff";
  return {
    "color-scheme": theme.scheme,
    "--panel": colors.panel,
    "--ink": colors.ink,
    "--muted": colors.muted,
    "--line": colors.line,
    "--hover": colors.hover,
    "--field": field,
    "--accent": colors.accent,
    "--accent-ink": colors.accentInk,
    "--link": colors.link ?? colors.accent,
    "--map-bg": colors.mapBg,
    "--start": colors.start,
    "--via": colors.via,
    "--end": colors.end,
    "--marker-ink": colors.markerInk,
    "--route": colors.route.join(", "),
    "--route-casing": colors.routeCasing,
    "--ghost": translucent(colors.routeCasing, 0.7), // a snapped stop's dot
    "--attribution": translucent(field, 0.5), // the map's attribution: its strip
    "--attribution-ink": translucent(colors.ink, 0.75), // and its links
    "--baseline": colors.baseline,
    "--flash": colors.flash,
    "--highlight": colors.highlight,
    "--draw": colors.draw,
    "--par": colors.par,
    "--toast": colors.toast,
    "--toast-ink": colors.toastInk,
    "--error-bg": colors.errorBg,
    "--error-ink": colors.errorInk,
    "--font-display": fonts.display,
    "--font-body": fonts.body,
    "--font-numbers": fonts.numbers,
    // a bare 0 is no length inside min() and calc(), where style.css takes the radius too
    "--radius": shape.radius === "0" ? "0px" : shape.radius,
    "--marker-radius": shape.markerRadius,
    "--brand-case": shape.uppercase ? "uppercase" : "none",
  };
}

/** Put `theme` on `root` (the page's <html>); resolves once its fonts are in, or have failed. */
export async function applyTheme(
  theme: Theme,
  root: HTMLElement = document.documentElement,
): Promise<void> {
  root.dataset.theme = theme.id;
  for (const [name, value] of Object.entries(themeProperties(theme))) {
    root.style.setProperty(name, value);
  }
  // the weights style.css asks of each stack: the brand, headings and Go; the text; the ratio
  const { display, body, numbers } = theme.fonts;
  const fonts = root.ownerDocument.fonts;
  try {
    await theme.fonts.load();
    await Promise.all(
      [`700 1em ${display}`, `1em ${body}`, `800 1em ${numbers}`].map((f) => fonts.load(f)),
    );
  } catch (error) {
    console.debug(`the ${theme.id} theme's fonts did not load (${String(error)})`);
  }
}
