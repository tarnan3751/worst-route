import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeView, json, stubFetch } from "../__fixtures__/http";
import { loadStream } from "../__fixtures__/stream";
import type { RouteDocument, RouteRequest } from "../api/types";
import { ANSWER } from "../__fixtures__/challenges";
import { JobRunner, countsTitle, statLines } from "../run/controls";
import { followPortfolioMax } from "../settings/panel";
import { DEFAULT_SETTINGS, IDLE_RUN, ITEMS, Store, type AppState, type Settings } from "../state";
import { round6 } from "../util/geo";
import {
  UrlSync,
  decodeUrlState,
  encodeUrlState,
  followShareImage,
  openRoute,
  settingsFrom,
  geojsonUrl,
  shareImageUrl,
  urlStateOf,
  type UrlMode,
  type UrlState,
} from "./share";

const full: UrlState = {
  route: "5ea844bffd40",
  daily: null,
  challenge: null,
  seed: 12345,
  seeds: null,
  budgetS: 15,
  tier: "T2",
  leashKm: 6.5,
  target: { kind: "duration", seconds: 32400 },
  mode: null,
  turns: null,
  strict: null,
  hard: null,
  reorder: null,
  certified: null,
  vehicle: null,
  departure: null,
  region: null,
  stops: [
    { lat: 45.5231, lon: -122.6765 },
    { lat: 45.519368, lon: -122.662765 },
    { lat: 45.4817, lon: -122.6263 },
  ],
};

const nothing: UrlState = {
  route: null,
  daily: null,
  challenge: null,
  seed: null,
  seeds: null,
  budgetS: null,
  tier: null,
  leashKm: null,
  target: null,
  mode: null,
  turns: null,
  strict: null,
  hard: null,
  reorder: null,
  certified: null,
  vehicle: null,
  departure: null,
  region: null,
  stops: null,
};

describe("the URL state", () => {
  it("round-trips every field", () => {
    const query = encodeUrlState(full);
    expect(query).toBe(
      "?route=5ea844bffd40&seed=12345&budget=15&tier=T2&leash=6.5&target=duration:32400" +
        "&stops=45.5231,-122.6765;45.519368,-122.662765;45.4817,-122.6263",
    );
    expect(decodeUrlState(query)).toEqual(full);
  });

  it("round-trips auto, a seed of 0, the largest safe seed, and nothing", () => {
    const some = { ...nothing, seed: 0, leashKm: "auto" as const, tier: "auto" as const };
    expect(decodeUrlState(encodeUrlState(some))).toEqual(some);
    for (const target of [{ kind: "max" }, { kind: "ratio", value: 12.5 }] as const) {
      const state = { ...nothing, target };
      expect(decodeUrlState(encodeUrlState(state))).toEqual(state);
    }
    const big = { ...nothing, seed: Number.MAX_SAFE_INTEGER };
    expect(decodeUrlState(encodeUrlState(big))).toEqual(big);
    expect(encodeUrlState(nothing)).toBe("");
    expect(decodeUrlState("")).toEqual(nothing);
  });

  it("carries a pinned region, and none when the server picks it", () => {
    const pinned = { ...nothing, region: "us-t1", stops: full.stops };
    const query = encodeUrlState(pinned);
    expect(query).toBe(
      "?region=us-t1&stops=45.5231,-122.6765;45.519368,-122.662765;45.4817,-122.6263",
    );
    expect(decodeUrlState(query)).toEqual(pinned);
    expect(settingsFrom(decodeUrlState(query), DEFAULT_SETTINGS).region).toBe("us-t1");
    for (const region of ["", "../x", "US", "us t1", "-us"]) {
      expect(decodeUrlState(`?region=${encodeURIComponent(region)}`).region).toBeNull();
    }
    const state = {
      regions: null,
      region: null,
      pick: null,
      stops: [],
      settings: { ...DEFAULT_SETTINGS, region: "oregon" },
      run: IDLE_RUN,
      route: null,
      error: null,
      brands: null,
      challenge: null,
    };
    expect(urlStateOf(state).region).toBe("oregon");
    expect(urlStateOf({ ...state, settings: DEFAULT_SETTINGS }).region).toBeNull();
  });

  it("carries ?daily, today's route to open, which the app's own state never writes", () => {
    expect(decodeUrlState("?daily")).toEqual({ ...nothing, daily: true });
    expect(decodeUrlState("?daily=1&seed=2")).toEqual({ ...nothing, daily: true, seed: 2 });
    expect(encodeUrlState({ ...nothing, daily: true })).toBe("?daily");
    expect(decodeUrlState("?dailyx").daily).toBeNull();
    const state: AppState = {
      regions: null,
      region: null,
      pick: null,
      stops: [],
      settings: DEFAULT_SETTINGS,
      run: IDLE_RUN,
      route: null,
      error: null,
      brands: null,
      challenge: null,
    };
    expect(urlStateOf(state).daily).toBeNull(); // once opened, a route like any other
  });

  it("carries ?challenge, the challenge to set up, written only while one is set up", () => {
    const query = "?challenge=bridges-2026-11-02&stops=40.024,-85.18;39.986,-87.148";
    const set = decodeUrlState(query);
    expect(set).toMatchObject({ challenge: "bridges-2026-11-02", route: null, daily: null });
    expect(encodeUrlState(set)).toBe(query);
    for (const id of ["", "Bridges-2026-11-02", "../x", "-bridges-2026-11-02", "a b"]) {
      expect(decodeUrlState(`?challenge=${encodeURIComponent(id)}`).challenge).toBeNull();
    }
    for (const { id } of ANSWER.challenges) {
      expect(decodeUrlState(`?challenge=${id}`).challenge).toBe(id);
    }
    const state: AppState = {
      regions: null,
      region: null,
      pick: null,
      stops: [],
      settings: DEFAULT_SETTINGS,
      run: IDLE_RUN,
      route: null,
      error: null,
      brands: null,
      challenge: null,
    };
    expect(urlStateOf(state).challenge).toBeNull();
    expect(encodeUrlState(urlStateOf({ ...state, challenge: "long-way-2026-11-02" }))).toBe(
      "?challenge=long-way-2026-11-02&budget=20&tier=auto&leash=auto&target=max",
    );
    const written: string[] = [];
    const store = new Store(state);
    new UrlSync(store, (url) => written.push(url)).start(false);
    store.update({ challenge: "hard-mode-2026-11-01" });
    store.update({ challenge: null });
    expect(written).toEqual([
      "?challenge=hard-mode-2026-11-01&budget=20&tier=auto&leash=auto&target=max",
      "?budget=20&tier=auto&leash=auto&target=max",
    ]);
  });

  it("keeps clicked coordinates exactly", () => {
    // Coordinates as a click gives them, rounded as the stop picker rounds them.
    const clicks = [
      [45.52347512345678, -122.67665398765432],
      [45.250000499, -123.0499995],
      [44.0000004, -121.9999996],
      [45.7, -122.35],
    ].map(([lat, lon]) => ({ lat: round6(lat), lon: round6(lon) }));
    const state = { ...nothing, stops: clicks };
    expect(decodeUrlState(encodeUrlState(state)).stops).toEqual(clicks);
  });

  it("ignores what it cannot use", () => {
    const query = "?route=../x&seed=-1&budget=500&tier=T9&leash=0&stops=45.5,-122.6;nonsense";
    expect(decodeUrlState(query)).toEqual(nothing);
    expect(decodeUrlState("?seed=1.5&budget=abc&leash=151&stops=91,0").seed).toBeNull();
    expect(decodeUrlState("?seed=&budget=").budgetS).toBeNull();
    expect(decodeUrlState("?seed=9007199254740993").seed).toBeNull();
    expect(decodeUrlState("?stops=45.5,-122.6,3").stops).toBeNull();
    for (const target of [
      "ratio:1",
      "ratio:",
      "duration:0",
      "duration:604801",
      "speed:3",
      "max:1",
    ]) {
      expect(decodeUrlState(`?target=${target}`).target).toBeNull();
    }
  });

  it("is what the app shows, and gives the settings back", () => {
    const state: AppState = {
      region: null,
      stops: full.stops!.map((stop) => ({ ...stop, snapped: null })),
      settings: {
        ...DEFAULT_SETTINGS,
        seed: 12345,
        budgetS: 15,
        tier: "T2",
        leashKm: 6.5,
        target: "duration",
        durationS: 32400,
      },
      run: IDLE_RUN,
      route: null,
      error: null,
      brands: null,
      challenge: null,
      regions: null,
      pick: null,
    };
    const url = decodeUrlState(encodeUrlState(urlStateOf(state)));
    expect(url).toEqual({ ...full, route: null });
    expect(settingsFrom(url, { ...DEFAULT_SETTINGS, units: "km" })).toEqual({
      ...state.settings,
      units: "km",
    });
    expect(settingsFrom(nothing, DEFAULT_SETTINGS)).toEqual(DEFAULT_SETTINGS);
  });
});

describe("opening a ?route= link at startup", () => {
  const route = loadStream().route;
  const query = `?route=${route.route_id}&seed=9&budget=5`;

  /** The app as main.ts starts it on `query`: the store, and the URL's writes. */
  function start() {
    const url = decodeUrlState(query);
    const store = new Store({
      region: null,
      stops: [],
      settings: settingsFrom(url, DEFAULT_SETTINGS),
      run: IDLE_RUN,
      route: null,
      error: null,
      brands: null,
      challenge: null,
      regions: null,
      pick: null,
    });
    const writes: string[] = [];
    const sync = new UrlSync(store, (q) => writes.push(q));
    return { url, store, writes, sync, runner: new JobRunner(store, fakeView(), () => {}) };
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps the URL until the route has opened, then writes the route's request", async () => {
    const baseline = { type: "LineString", coordinates: route.geometry.coordinates.slice(0, 2) };
    stubFetch((call) =>
      call.url.endsWith("/geojson")
        ? json({
            type: "FeatureCollection",
            features: [{ type: "Feature", geometry: baseline, properties: { kind: "baseline" } }],
          })
        : json(route),
    );
    const { url, store, writes, sync, runner } = start();
    const opened = await openRoute(url.route!, store, runner);
    expect(opened).toBe(true);
    expect(writes).toEqual([]);
    sync.start(opened);
    expect(writes).toEqual([
      `?route=${route.route_id}&seed=1&budget=6&tier=auto&leash=auto&target=max` +
        "&stops=45.5231,-122.6765;45.5195,-122.6625;45.5317,-122.654",
    ]);
    expect(decodeUrlState(writes[0]).route).toBe(route.route_id);
  });

  it("keeps the parameter of a link that does not open, and says why", async () => {
    stubFetch(() => json({ code: "NOT_FOUND", message: `no route ${route.route_id}` }, 404));
    const { url, store, writes, sync, runner } = start();
    const opened = await openRoute(url.route!, store, runner);
    expect(opened).toBe(false);
    sync.start(opened);
    expect(writes).toEqual([]);
    expect(store.state.error?.code).toBe("NOT_FOUND");
    store.updateSettings({ budgetS: 7 }); // the user moves on: the URL follows
    expect(writes).toEqual(["?seed=9&budget=7&tier=auto&leash=auto&target=max"]);
  });
});

describe("a ratio-target route opened by its link", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("takes its target into the settings and the URL", async () => {
    const route = loadStream("portland-ratio-stream").route;
    stubFetch((call) =>
      call.url.endsWith("/geojson")
        ? json({ type: "FeatureCollection", features: [] })
        : json(route),
    );
    const store = new Store({
      region: null,
      stops: [],
      settings: DEFAULT_SETTINGS,
      run: IDLE_RUN,
      route: null,
      error: null,
      brands: null,
      challenge: null,
      regions: null,
      pick: null,
    });
    expect(await openRoute(route.route_id, store, new JobRunner(store, fakeView(), () => {}))).toBe(
      true,
    );
    expect(store.state.settings).toMatchObject({ target: "ratio", ratio: 25 });
    expect(store.state.run.outcome).toMatchObject({ kind: "ratio", within_tolerance: true });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&target=ratio:25&");
  });
});

describe("the share image", () => {
  it("is the API's share.png of the route, through the /api proxy", () => {
    expect(shareImageUrl("5ea844bffd40")).toBe("/api/routes/5ea844bffd40/share.png");
    expect(shareImageUrl("5ea844bffd40", "night")).toBe(
      "/api/routes/5ea844bffd40/share.png?theme=night",
    );
  });

  it("is in the theme the page shows, followed as the setting and the system change", () => {
    let changed = () => {};
    const scheme = {
      matches: false, // a light system
      addEventListener: (_type: string, listener: () => void) => (changed = listener),
    } as unknown as Pick<MediaQueryList, "matches" | "addEventListener">;
    const store = new Store(emptyApp()); // the theme setting "auto"
    const shown: (string | null)[] = [];
    followShareImage(store, (url) => shown.push(url), scheme);
    expect(shown).toEqual([null]); // no route shown
    const route = loadStream("portland-stream").route;
    store.update({ route });
    const url = `/api/routes/${route.route_id}/share.png?theme=`;
    expect(shown.at(-1)).toBe(`${url}studio`); // auto by day
    (scheme as { matches: boolean }).matches = true;
    changed();
    expect(shown.at(-1)).toBe(`${url}night`); // auto by night
    store.updateSettings({ theme: "ledger" });
    expect(shown.at(-1)).toBe(`${url}ledger`);
    store.updateSettings({ theme: "classic" });
    expect(shown.at(-1)).toBe(`${url}classic`);
    store.update({ route: null });
    expect(shown.at(-1)).toBeNull();
  });
});

describe("the mode and the turns in the URL", () => {
  const modes: UrlMode[] = [
    { objective: "distance" },
    { objective: "time" },
    ...ITEMS.map((item): UrlMode => ({ objective: "collect", item })),
    { objective: "brand", brand: 229 },
    { objective: "brand", brand: 0 },
    { objective: "alphabet" },
  ];

  it("round-trips every mode and turn setting", () => {
    for (const mode of modes) {
      for (const turns of [null, "no_left", "no_right"] as const) {
        for (const strict of turns === null ? [null] : [null, true as const]) {
          for (const hard of [null, true as const]) {
            const state = { ...nothing, mode, turns, strict, hard };
            expect(decodeUrlState(encodeUrlState(state))).toEqual(state);
          }
        }
      }
    }
    expect(
      encodeUrlState({ ...nothing, mode: { objective: "brand", brand: 229 }, hard: true }),
    ).toBe("?mode=brand:229&hard=1");
    expect(encodeUrlState({ ...nothing, mode: { objective: "alphabet" } })).toBe("?mode=alphabet");
    const collecting = { objective: "collect", item: "toll_booth" } as const;
    expect(encodeUrlState({ ...nothing, mode: collecting, turns: "no_right", strict: true })).toBe(
      "?mode=collect:toll_booth&turns=right&strict=1",
    );
    expect(encodeUrlState({ ...nothing, mode: { objective: "time" }, turns: "no_left" })).toBe(
      "?mode=time&turns=left",
    );
  });

  it("ignores what it cannot use", () => {
    for (const mode of [
      "collect",
      "collect:",
      "collect:bridges",
      "collect:bridge:2",
      "time:bridge",
      "distance:",
      "brand",
      "brand:",
      "brand:starbucks",
      "brand:-1",
      "brand:1.5",
      "brand:1e3",
      "brand: 229",
      "brand:229:1",
      "brand:9007199254740993",
      "alphabet:1",
      "Alphabet",
      "slowest",
      "",
    ]) {
      expect(decodeUrlState(`?mode=${mode}`).mode).toBeNull();
    }
    for (const hard of ["true", "0", "yes", ""]) {
      expect(decodeUrlState(`?hard=${hard}`).hard).toBeNull();
    }
    for (const turns of ["no_left", "Left", "both", "", "1"]) {
      expect(decodeUrlState(`?turns=${turns}`).turns).toBeNull();
    }
    expect(decodeUrlState("?strict=1").strict).toBeNull(); // strict needs a side
    for (const strict of ["true", "0", "yes", ""]) {
      expect(decodeUrlState(`?turns=left&strict=${strict}`).strict).toBeNull();
    }
  });

  it("reads a ratio target beside a collect mode as as bad as possible", () => {
    const url = decodeUrlState("?target=ratio:20&mode=collect:bridge");
    expect(url).toMatchObject({ target: { kind: "max" }, mode: { objective: "collect" } });
    expect(settingsFrom(url, DEFAULT_SETTINGS)).toMatchObject({
      objective: "collect",
      target: "max",
    });
    const ratio = { kind: "ratio", value: 20 };
    expect(decodeUrlState("?target=ratio:20&mode=time").target).toEqual(ratio);
    expect(decodeUrlState("?target=ratio:20&mode=collect").target).toEqual(ratio); // no mode
    expect(decodeUrlState("?target=duration:3600&mode=collect:bridge").target).toEqual({
      kind: "duration",
      seconds: 3600,
    });
  });

  it("reads a ratio beside a brand, and any target beside the alphabet route, as max", () => {
    const max = { kind: "max" };
    expect(decodeUrlState("?target=ratio:20&mode=brand:229").target).toEqual(max);
    expect(decodeUrlState("?target=duration:3600&mode=brand:229").target).toEqual({
      kind: "duration",
      seconds: 3600,
    });
    for (const target of ["ratio:20", "duration:3600", "max"]) {
      expect(decodeUrlState(`?target=${target}&mode=alphabet`).target).toEqual(max);
    }
    expect(decodeUrlState("?mode=alphabet").target).toBeNull();
    const url = decodeUrlState("?target=ratio:20&mode=alphabet");
    expect(settingsFrom(url, DEFAULT_SETTINGS)).toMatchObject({
      objective: "alphabet",
      target: "max",
      ratio: 20, // kept for another mode, as the panel keeps it
    });
  });

  it("is what the app shows, without the defaults, and gives the settings back", () => {
    const shown = (patch: Partial<Settings>): AppState => ({
      region: null,
      stops: [],
      settings: { ...DEFAULT_SETTINGS, ...patch },
      run: IDLE_RUN,
      route: null,
      error: null,
      brands: null,
      challenge: null,
      regions: null,
      pick: null,
    });
    const settings: Settings = {
      ...DEFAULT_SETTINGS,
      objective: "collect",
      item: "roundabout",
      turns: "no_right",
      strict: true,
    };
    const query = encodeUrlState(urlStateOf(shown(settings)));
    expect(query).toContain("&target=max&mode=collect:roundabout&turns=right&strict=1");
    expect(settingsFrom(decodeUrlState(query), DEFAULT_SETTINGS)).toEqual(settings);
    const slowest = { ...DEFAULT_SETTINGS, objective: "time", turns: "no_left" } as const;
    const back = settingsFrom(decodeUrlState(encodeUrlState(urlStateOf(shown(slowest)))), {
      ...DEFAULT_SETTINGS,
      item: "toll_booth", // the URL leaves the item alone under another mode
    });
    expect(back).toEqual({ ...slowest, item: "toll_booth" });
    expect(urlStateOf(shown({}))).toMatchObject({
      mode: null,
      turns: null,
      strict: null,
      hard: null,
    });
    expect(urlStateOf(shown({ strict: true })).strict).toBeNull(); // no side, no strict
  });

  it("carries a brand's pilgrimage, the alphabet route and hard mode, and gives them back", () => {
    const shown = (patch: Partial<Settings>): AppState => ({
      ...emptyApp(),
      settings: { ...DEFAULT_SETTINGS, ...patch },
    });
    const pilgrimage: Settings = {
      ...DEFAULT_SETTINGS,
      objective: "brand",
      brand: 229,
      hard: true,
    };
    const query = encodeUrlState(urlStateOf(shown(pilgrimage)));
    expect(query).toMatch(/&target=max&mode=brand:229&hard=1$/);
    expect(settingsFrom(decodeUrlState(query), DEFAULT_SETTINGS)).toEqual(pilgrimage);
    const alphabet: Settings = { ...DEFAULT_SETTINGS, objective: "alphabet", brand: 229 };
    const letters = encodeUrlState(urlStateOf(shown(alphabet)));
    expect(letters).toMatch(/&target=max&mode=alphabet$/);
    expect(letters).not.toContain("hard=");
    // The URL leaves the brand alone under another mode, as it does the item.
    const back = settingsFrom(decodeUrlState(letters), { ...DEFAULT_SETTINGS, brand: 7 });
    expect(back).toEqual({ ...alphabet, brand: 7 });
    // A pilgrimage whose brand is not known yet (the brands not fetched) writes no mode.
    expect(urlStateOf(shown({ objective: "brand", brand: null })).mode).toBeNull();
    expect(urlStateOf(shown({ hard: true, turns: "no_left" }))).toMatchObject({
      turns: "no_left",
      strict: null,
      hard: true,
    });
  });

  it("carries the worst order only when on, as reorder=1, and gives it back", () => {
    const shown = (patch: Partial<Settings>): AppState => ({
      ...emptyApp(),
      settings: { ...DEFAULT_SETTINGS, ...patch },
    });
    const worst: Settings = { ...DEFAULT_SETTINGS, reorder: true, hard: true };
    const query = encodeUrlState(urlStateOf(shown(worst)));
    expect(query).toMatch(/&target=max&hard=1&reorder=1$/);
    expect(settingsFrom(decodeUrlState(query), DEFAULT_SETTINGS)).toEqual(worst);
    expect(encodeUrlState(urlStateOf(shown({})))).not.toContain("reorder");
    expect(decodeUrlState("?reorder=1").reorder).toBe(true);
    for (const value of ["true", "0", "yes", ""]) {
      expect(decodeUrlState(`?reorder=${value}`).reorder).toBeNull();
    }
  });

  it("carries Certified only when on, as certified=1, and gives it back", () => {
    const shown = (patch: Partial<Settings>): AppState => ({
      ...emptyApp(),
      settings: { ...DEFAULT_SETTINGS, ...patch },
    });
    const certified: Settings = { ...DEFAULT_SETTINGS, certified: true, leashKm: 0.4 };
    const query = encodeUrlState(urlStateOf(shown(certified)));
    expect(query).toMatch(/&leash=0.4&target=max&certified=1$/);
    expect(settingsFrom(decodeUrlState(query), DEFAULT_SETTINGS)).toEqual(certified);
    expect(encodeUrlState(urlStateOf(shown({})))).not.toContain("certified");
    for (const value of ["true", "0", "yes", ""]) {
      expect(decodeUrlState(`?certified=${value}`).certified).toBeNull();
    }
  });

  it("carries a portfolio's seeds only above one, as seeds=4, and gives them back", () => {
    const shown = (patch: Partial<Settings>): AppState => ({
      ...emptyApp(),
      settings: { ...DEFAULT_SETTINGS, ...patch },
    });
    const four: Settings = { ...DEFAULT_SETTINGS, seed: 40, portfolio: 4 };
    const query = encodeUrlState(urlStateOf(shown(four)));
    expect(query).toMatch(/^\?seed=40&seeds=4&budget=/);
    expect(settingsFrom(decodeUrlState(query), DEFAULT_SETTINGS)).toEqual(four);
    expect(encodeUrlState(urlStateOf(shown({})))).not.toContain("seeds");
    for (const value of ["1", "5", "2.5", "two", ""]) {
      expect(decodeUrlState(`?seeds=${value}`).seeds).toBeNull();
    }
  });
});

describe("a route of another mode opened by its link", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** Open `route` by its link, from settings of another mode; the store. */
  async function open(route: RouteDocument): Promise<Store> {
    stubFetch((call) =>
      call.url.endsWith("/geojson")
        ? json({ type: "FeatureCollection", features: [] })
        : json(route),
    );
    const store = new Store({
      region: null,
      stops: [],
      settings: { ...DEFAULT_SETTINGS, objective: "collect", item: "roundabout", turns: "no_left" },
      run: IDLE_RUN,
      route: null,
      error: null,
      brands: null,
      challenge: null,
      regions: null,
      pick: null,
    });
    const runner = new JobRunner(store, fakeView(), () => {});
    expect(await openRoute(route.route_id, store, runner)).toBe(true);
    return store;
  }

  it("takes a slowest-time route's mode into the settings, the URL and the stat line", async () => {
    const store = await open(loadStream("portland-time-stream").route);
    expect(store.state.settings).toMatchObject({
      objective: "time",
      item: "roundabout", // kept for the next collect run
      turns: "any",
      strict: false,
    });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&target=max&mode=time&stops=");
    const [ratio, , , , drive, items, turns] = statLines(store.state);
    expect([ratio, drive, items, turns]).toEqual([
      "16.9× slower",
      "drive 4 h 8 min + delays 1 h 59 min",
      "",
      "277 left · 282 right",
    ]);
  });

  it("takes a recorded pilgrimage's brand and an alphabet route's mode, with their rows", async () => {
    const pilgrimage = await open(loadStream("portland-brand-stream").route);
    expect(pilgrimage.state.settings).toMatchObject({ objective: "brand", brand: 229 });
    expect(encodeUrlState(urlStateOf(pilgrimage.state))).toContain("&mode=brand:229&stops=");
    expect(statLines(pilgrimage.state)[5]).toBe("14 of 19 Starbucks");
    const alphabet = await open(loadStream("portland-alphabet-stream").route);
    expect(alphabet.state.settings).toMatchObject({ objective: "alphabet", target: "max" });
    expect(encodeUrlState(urlStateOf(alphabet.state))).toContain("&target=max&mode=alphabet&");
    expect(statLines(alphabet.state)[5]).toBe("letters: 15 (A–O)");
    expect(countsTitle(alphabet.state).split("\n")[1]).toBe("B: West Burnside Street");
  });

  it("takes a collecting route's item, and a route's turn constraint", async () => {
    const { route } = loadStream("portland-collect-stream");
    const store = await open(route);
    expect(store.state.settings).toMatchObject({ objective: "collect", item: "bridge" });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&mode=collect:bridge&stops=");
    expect(statLines(store.state)[5]).toBe("18 bridges");
    const constraints = { no_left: false, no_right: true, strict: true };
    const strict = await open({ ...route, request: { ...route.request, constraints } });
    expect(strict.state.settings).toMatchObject({ turns: "no_right", strict: true });
    expect(encodeUrlState(urlStateOf(strict.state))).toContain("&turns=right&strict=1&");
  });
});

describe("a pilgrimage, an alphabet route and a hard-mode route opened by their links", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const { route } = loadStream("portland-collect-stream");

  /** Open `route` with `request` patched in, from a panel with `settings`; the store. */
  async function open(request: Partial<RouteRequest>, settings: Partial<Settings> = {}) {
    const patched = { ...route, request: { ...route.request, item: null, ...request } };
    stubFetch((call) =>
      call.url.endsWith("/geojson")
        ? json({ type: "FeatureCollection", features: [] })
        : json(patched),
    );
    const store = new Store({ ...emptyApp(), settings: { ...DEFAULT_SETTINGS, ...settings } });
    expect(await openRoute(route.route_id, store, new JobRunner(store, fakeView(), () => {}))).toBe(
      true,
    );
    return store;
  }

  it("takes a pilgrimage's brand into the settings and the URL", async () => {
    const store = await open(
      { objective: "brand", brand: 229 },
      { brand: 261, item: "roundabout" },
    );
    expect(store.state.settings).toMatchObject({
      objective: "brand",
      brand: 229,
      item: "roundabout",
    });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&mode=brand:229&stops=");
    expect(store.state.run.mode).toMatchObject({ objective: "brand", brand: 229 });
  });

  it("takes an alphabet route's mode, and keeps the panel's brand", async () => {
    const store = await open({ objective: "alphabet", brand: null }, { brand: 261, hard: true });
    expect(store.state.settings).toMatchObject({ objective: "alphabet", brand: 261, hard: false });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&target=max&mode=alphabet&stops=");
  });

  it("takes hard mode from the request's constraints, alone or with a side", async () => {
    const hard = { no_left: false, no_right: false, strict: false, hard: true };
    const alone = await open({ objective: "distance", constraints: hard });
    expect(alone.state.settings).toMatchObject({ turns: "any", strict: false, hard: true });
    expect(encodeUrlState(urlStateOf(alone.state))).toContain("&hard=1&stops=");
    const sided = await open({ objective: "distance", constraints: { ...hard, no_right: true } });
    expect(sided.state.settings).toMatchObject({ turns: "no_right", hard: true });
    expect(encodeUrlState(urlStateOf(sided.state))).toContain("&turns=right&hard=1&");
    const without = await open({ objective: "distance" }, { hard: true });
    expect(without.state.settings.hard).toBe(false);
  });

  it("takes the worst order, the stops in the order the route visits them", async () => {
    // a stored worst-order route's request has its stops in its route's order, as its stops
    const visited = route.request.stops ?? [];
    const store = await open({ objective: "distance", reorder: true });
    expect(store.state.settings.reorder).toBe(true);
    expect(store.state.stops.map(({ lat, lon }) => ({ lat, lon }))).toEqual(
      visited.map(({ lat, lon }) => ({ lat, lon })),
    );
    expect(store.state.stops.map(({ snapped }) => snapped?.node)).toEqual(
      route.stops.map(({ node }) => node),
    );
    expect(store.state.run.order).toBeNull(); // the list is in the route's order: no "visited"
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&reorder=1&stops=");
    const plain = await open({ objective: "distance" }, { reorder: true });
    expect(plain.state.settings.reorder).toBe(false);
  });

  it("takes Certified from a certified route, and unticks it for another", async () => {
    const store = await open({ objective: "distance", certified: true });
    expect(store.state.settings.certified).toBe(true);
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&certified=1&stops=");
    const plain = await open({ objective: "distance" }, { certified: true });
    expect(plain.state.settings.certified).toBe(false);
  });

  it("takes a portfolio's seeds, its first seed into the panel, its winner's on the stats", async () => {
    const won: RouteDocument = {
      ...route,
      seed: 42,
      request: { ...route.request, item: null, objective: "distance", seed: 40, portfolio: 4 },
      run: { ...route.run, portfolio: { k: 4, seeds: [40, 41, 42, 43], winner: 2 } },
    };
    stubFetch((call) =>
      call.url.endsWith("/geojson") ? json({ type: "FeatureCollection", features: [] }) : json(won),
    );
    const store = new Store(emptyApp());
    await openRoute(won.route_id, store, new JobRunner(store, fakeView(), () => {}));
    expect(store.state.settings).toMatchObject({ seed: 40, portfolio: 4 });
    // a server that runs two seeds lowers them, as it does a link's, the stats still the route's
    const capped = new Store(emptyApp());
    await followPortfolioMax(capped, Promise.resolve(2));
    await openRoute(won.route_id, capped, new JobRunner(capped, fakeView(), () => {}));
    expect(capped.state.settings).toMatchObject({ seed: 40, portfolio: 2 });
    expect(statLines(capped.state)[3]).toMatch(/ · best of 4 runs · seed 42$/);
    expect(statLines(store.state)[3]).toMatch(/ · best of 4 runs · seed 42$/);
    expect(encodeUrlState(urlStateOf(store.state))).toContain(`=${won.route_id}&seed=40&seeds=4&`);
    const single = await open({ objective: "distance" }, { portfolio: 3 });
    expect(single.state.settings.portfolio).toBe(1);
    expect(statLines(single.state)[3]).not.toContain("best of");
  });
});

describe("the vehicle in the URL", () => {
  const shown = (patch: Partial<Settings>): AppState => ({
    region: null,
    stops: [],
    settings: { ...DEFAULT_SETTINGS, ...patch },
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
    challenge: null,
    regions: null,
    pick: null,
  });

  it("round-trips a vehicle, and leaves the default out", () => {
    const state = { ...nothing, vehicle: { mpg: 31, gas_price_usd: 4.25 } };
    expect(encodeUrlState(state)).toBe("?vehicle=31,4.25");
    expect(decodeUrlState(encodeUrlState(state))).toEqual(state);
    for (const limits of ["5,0.5", "100,20"]) {
      expect(decodeUrlState(`?vehicle=${limits}`).vehicle).not.toBeNull();
    }
    expect(urlStateOf(shown({})).vehicle).toBeNull();
    const priced = shown({ gasPriceUsd: 4.25 });
    expect(encodeUrlState(urlStateOf(priced))).toContain("&vehicle=24,4.25");
    expect(
      settingsFrom(decodeUrlState(encodeUrlState(urlStateOf(priced))), DEFAULT_SETTINGS),
    ).toEqual(priced.settings);
  });

  it("ignores what it cannot use", () => {
    for (const vehicle of [
      "4,3",
      "101,3",
      "24,0.4",
      "24,21",
      "24",
      "24,3.89,1",
      "abc,3",
      ",3.89",
      "",
    ]) {
      expect(decodeUrlState(`?vehicle=${vehicle}`).vehicle).toBeNull();
    }
    expect(settingsFrom(decodeUrlState("?vehicle=4,3"), DEFAULT_SETTINGS)).toEqual(
      DEFAULT_SETTINGS,
    );
  });
});

describe("a route's vehicle opened by its link", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function open(route: RouteDocument): Promise<Store> {
    stubFetch((call) =>
      call.url.endsWith("/geojson")
        ? json({ type: "FeatureCollection", features: [] })
        : json(route),
    );
    const store = new Store({
      region: null,
      stops: [],
      settings: { ...DEFAULT_SETTINGS, mpg: 40, gasPriceUsd: 5 },
      run: IDLE_RUN,
      route: null,
      error: null,
      brands: null,
      challenge: null,
      regions: null,
      pick: null,
    });
    expect(await openRoute(route.route_id, store, new JobRunner(store, fakeView(), () => {}))).toBe(
      true,
    );
    return store;
  }

  it("takes the stored request's vehicle into the settings and the URL", async () => {
    const { route } = loadStream("portland-directions-stream");
    const vehicle = { mpg: 31, gas_price_usd: 4.25 };
    const store = await open({ ...route, request: { ...route.request, vehicle } });
    expect(store.state.settings).toMatchObject({ mpg: 31, gasPriceUsd: 4.25 });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&vehicle=31,4.25&");
    const recorded = await open(route); // 24 mpg at $3.89, the default: left out of the URL
    expect(recorded.state.settings).toMatchObject({ mpg: 24, gasPriceUsd: 3.89 });
    expect(encodeUrlState(urlStateOf(recorded.state))).not.toContain("vehicle=");
  });

  it("keeps the panel's vehicle for a route whose request has none, or one it does not take", async () => {
    const store = await open(loadStream("portland-time-stream").route);
    expect(store.state.settings).toMatchObject({ mpg: 40, gasPriceUsd: 5 });
    const { route } = loadStream("portland-directions-stream");
    for (const vehicle of [
      { mpg: 3, gas_price_usd: 3.89 },
      { mpg: 24, gas_price_usd: 0.1 },
    ]) {
      const scripted = await open({ ...route, request: { ...route.request, vehicle } });
      expect(scripted.state.settings).toMatchObject({ mpg: 40, gasPriceUsd: 5 });
      expect(encodeUrlState(urlStateOf(scripted.state))).toContain("&vehicle=40,5&");
    }
  });
});

describe("the departure in the URL and in a route's link", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("round-trips a departure with its offset, and ignores what is not one", () => {
    for (const departure of [
      "2026-10-31T09:00:00-07:00",
      "2026-10-31T09:00+05:30",
      "2026-11-01T02:00:00Z",
    ]) {
      const state = { ...nothing, departure };
      expect(decodeUrlState(encodeUrlState(state))).toEqual(state);
    }
    expect(encodeUrlState({ ...nothing, departure: "2026-10-31T09:00:00+05:30" })).toBe(
      "?departure=2026-10-31T09%3A00%3A00%2B05%3A30",
    );
    for (const text of ["2026-10-31T09:00", "2026-10-31", "tomorrow", "2026-13-31T09:00:00Z", ""]) {
      expect(decodeUrlState(`?departure=${encodeURIComponent(text)}`).departure).toBeNull();
    }
    const url = decodeUrlState("?departure=2026-10-31T09%3A00%3A00-07%3A00");
    expect(settingsFrom(url, DEFAULT_SETTINGS).departure).toBe("2026-10-31T09:00:00-07:00");
    expect(urlStateOf({ ...emptyApp(), settings: DEFAULT_SETTINGS }).departure).toBeNull(); // now
  });

  it("takes a stored request's departure into the settings, and keeps the panel's without one", async () => {
    const { route } = loadStream("portland-directions-stream");
    const open = async (departure: string | null) => {
      stubFetch((call) =>
        call.url.endsWith("/geojson")
          ? json({ type: "FeatureCollection", features: [] })
          : json({ ...route, request: { ...route.request, departure } }),
      );
      const store = new Store({
        ...emptyApp(),
        settings: { ...DEFAULT_SETTINGS, departure: "2026-11-02T08:00:00-08:00" },
      });
      expect(
        await openRoute(route.route_id, store, new JobRunner(store, fakeView(), () => {})),
      ).toBe(true);
      return store;
    };
    const stored = await open("2026-10-31T09:00:00-07:00");
    expect(stored.state.settings.departure).toBe("2026-10-31T09:00:00-07:00");
    expect(encodeUrlState(urlStateOf(stored.state))).toContain(
      "&departure=2026-10-31T09%3A00%3A00-07%3A00&",
    );
    const none = await open(null);
    expect(none.state.settings.departure).toBe("2026-11-02T08:00:00-08:00");
  });

  it("downloads the route's GeoJSON from the API, through the /api proxy", () => {
    expect(geojsonUrl("5ea844bffd40")).toBe("/api/routes/5ea844bffd40/geojson");
  });
});

/** An app with nothing in it. */
function emptyApp(): AppState {
  return {
    region: null,
    stops: [],
    settings: DEFAULT_SETTINGS,
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
    challenge: null,
    regions: null,
    pick: null,
  };
}
