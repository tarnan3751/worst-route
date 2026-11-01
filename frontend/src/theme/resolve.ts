// The theme the settings name (docs/reference/frontend.md, **Themes**): one of THEMES by its id,
// or "auto", studio while the system is light and night while it is dark, followed as the system
// changes. The setting is the device's: kept in the browser's storage under THEME_KEY, never in a
// link, every read and write in a try (a private window, storage blocked), so the page works
// without it and the setting then lasts as long as the page.
import { THEMES } from "./presets";
import type { Theme, ThemeId } from "./theme";
import type { Store } from "../state";

export const DEFAULT_THEME: ThemeId = "studio";
export const THEME_KEY = "worst-route.theme";
const DARK = "(prefers-color-scheme: dark)";

/** A theme by its id, or "auto". */
export type ThemeSetting = ThemeId | "auto";
/** Where the setting is kept: the browser's localStorage, or a stand-in. */
export type ThemeStorage = Pick<Storage, "getItem" | "setItem">;
/** Whether the system is dark, and its changes: `matchMedia(DARK)`, or a stand-in. */
export type SchemeQuery = Pick<MediaQueryList, "matches" | "addEventListener">;

/** The theme `setting` names, the system `dark` or not; an id no theme has gives the default. */
export function resolveTheme(setting: string, dark: boolean): Theme {
  const id = setting === "auto" ? (dark ? "night" : "studio") : setting;
  return (
    THEMES.find((theme) => theme.id === id) ??
    (THEMES.find((theme) => theme.id === DEFAULT_THEME) as Theme)
  );
}

/** The setting `storage` keeps: "auto" when none, or none it can read, or not a theme's id. */
export function storedTheme(storage: () => ThemeStorage = () => localStorage): ThemeSetting {
  try {
    const value = storage().getItem(THEME_KEY);
    if (value === "auto" || THEMES.some(({ id }) => id === value)) return value as ThemeSetting;
  } catch {
    // no storage: the default
  }
  return "auto";
}

/** Call `show` with the theme the store's settings name, now and whenever it changes: the setting
 * changed (and kept in `storage`) or, under "auto", the system's scheme. */
export function followTheme(
  store: Store,
  show: (theme: Theme) => void,
  scheme: SchemeQuery = matchMedia(DARK),
  storage: () => ThemeStorage = () => localStorage,
): void {
  let kept = store.state.settings.theme;
  let shown: Theme | null = null;
  const resolve = () => {
    const theme = resolveTheme(store.state.settings.theme, scheme.matches);
    if (theme === shown) return;
    shown = theme;
    show(theme);
  };
  store.watch(["settings"], ({ settings }) => {
    if (settings.theme !== kept) {
      kept = settings.theme;
      try {
        storage().setItem(THEME_KEY, kept);
      } catch {
        // the setting lasts as long as the page
      }
    }
    resolve();
  });
  scheme.addEventListener("change", resolve);
}
