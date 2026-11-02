// The settings' Theme row (docs/reference/frontend.md, **Themes**): auto, then every theme by its
// name. Never locked: a theme changes nothing in a request, so it can change while a job runs.
import type { Store } from "../state";
import { THEMES } from "../theme/presets";
import type { ThemeSetting } from "../theme/resolve";
import type { Theme } from "../theme/theme";
import { el } from "../util/dom";

export const AUTO_LABEL = "Auto (studio by day, night by night)";

/** The picker's options: auto, then each theme by its name, in their order. */
export function themeOptions(themes: readonly Theme[]): [ThemeSetting, string][] {
  return [
    ["auto", AUTO_LABEL],
    ...themes.map(({ id, name }): [ThemeSetting, string] => [id, name]),
  ];
}

/** The row, a field like the panel's others, kept in step with the store's settings. */
export function themeRow(store: Store): HTMLElement {
  const picker = el("select");
  for (const [value, text] of themeOptions(THEMES)) {
    const option = el("option", "", text);
    option.value = value;
    picker.append(option);
  }
  picker.addEventListener("change", () => {
    store.updateSettings({ theme: picker.value as ThemeSetting });
  });
  store.watch(["settings"], ({ settings }) => {
    picker.value = settings.theme;
  });
  const row = el("label", "field");
  row.append(el("span", "label", "Theme"), picker);
  return row;
}
