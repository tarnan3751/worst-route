// The theme the settings name: by its id, "auto" by the system's scheme (studio by day, night by
// night), an unknown id the default; kept in the browser's storage; followed as the setting and
// the system change.
import { describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
import { DEFAULT_THEME, THEME_KEY, followTheme, resolveTheme, storedTheme } from "./resolve";

function store(theme: string): Store {
  return new Store({
    regions: null,
    region: null,
    pick: null,
    stops: [],
    settings: { ...DEFAULT_SETTINGS, theme: theme as typeof DEFAULT_SETTINGS.theme },
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
    challenge: null,
  });
}

/** A stand-in for the system's scheme, `dark` or not, which `flip` changes. */
function scheme(dark: boolean) {
  let changed = () => {};
  const query = {
    matches: dark,
    addEventListener: (_type: string, listener: () => void) => (changed = listener),
  } as unknown as Pick<MediaQueryList, "matches" | "addEventListener">;
  return {
    query,
    flip: () => (((query as { matches: boolean }).matches = !query.matches), changed()),
  };
}

function storage(stored: Record<string, string> = {}) {
  return {
    stored,
    getItem: (key: string) => stored[key] ?? null,
    setItem: (key: string, value: string) => void (stored[key] = value),
  };
}

describe("resolveTheme", () => {
  it("gives auto studio by day and night by night, an id its theme", () => {
    expect(resolveTheme("auto", false).id).toBe("studio");
    expect(resolveTheme("auto", true).id).toBe("night");
    expect(resolveTheme("classic", true).id).toBe("classic");
    expect(resolveTheme("studio", true).id).toBe("studio");
  });

  it("falls back to the default for an id no theme has", () => {
    expect(DEFAULT_THEME).toBe("studio");
    expect(resolveTheme("sepia", false).id).toBe("studio");
    expect(resolveTheme("", true).id).toBe("studio");
  });
});

describe("storedTheme", () => {
  it("reads a theme's id or auto, else auto, storage or not", () => {
    expect(storedTheme(() => storage({ [THEME_KEY]: "classic" }))).toBe("classic");
    expect(storedTheme(() => storage({ [THEME_KEY]: "auto" }))).toBe("auto");
    expect(storedTheme(() => storage({ [THEME_KEY]: "sepia" }))).toBe("auto");
    expect(storedTheme(() => storage())).toBe("auto");
    const blocked = () => {
      throw new Error("SecurityError");
    };
    expect(storedTheme(blocked)).toBe("auto");
  });
});

describe("followTheme", () => {
  it("shows the theme at once, again as the system's scheme or the setting changes", () => {
    const s = store("auto");
    const system = scheme(false);
    const kept = storage();
    const shown: string[] = [];
    followTheme(
      s,
      (theme) => shown.push(theme.id),
      system.query,
      () => kept,
    );
    expect(shown).toEqual(["studio"]);
    expect(kept.stored).toEqual({}); // nothing changed, nothing written
    system.flip();
    expect(shown).toEqual(["studio", "night"]);
    s.updateSettings({ theme: "classic" });
    expect(shown).toEqual(["studio", "night", "classic"]);
    expect(kept.stored).toEqual({ [THEME_KEY]: "classic" });
    system.flip(); // a theme by its id follows no scheme
    s.updateSettings({ units: "km" }); // nor another setting
    expect(shown).toEqual(["studio", "night", "classic"]);
  });

  it("still shows a theme the storage cannot keep", () => {
    const s = store("auto");
    const shown: string[] = [];
    const blocked = () => {
      throw new Error("SecurityError");
    };
    followTheme(s, (theme) => shown.push(theme.id), scheme(true).query, blocked);
    s.updateSettings({ theme: "studio" });
    expect(shown).toEqual(["night", "studio"]);
  });
});
