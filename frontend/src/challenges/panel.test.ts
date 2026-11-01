// Setting a challenge up without a DOM: `ChallengePanel` over a real Store, today's route stubbed:
// the challenges listed for the regions served; Set it up pinning the region, putting in the stops
// (keeping a trip's own inside the region, else none) and the settings, the brand picked once the
// region's brands are known; the challenge staying set up while what it set is the store's (a run
// and its seed changing nothing) and no longer once a stop or a setting it fixes changes; today's
// challenge opening today's route, from a link too when GET /daily answers after /regions; refusals
// while busy or before today's route is done; and the section collapsing when a run starts.
import { describe, expect, it, vi } from "vitest";
import { settle } from "../__fixtures__/http";
import { loadStream } from "../__fixtures__/stream";
import type { BrandEntry, Region, RouteDocument } from "../api/types";
import type { DailyRoute } from "../daily/panel";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Settings, type Stop } from "../state";
import { CHALLENGES, type Challenge } from "./list";
import { ChallengePanel, setUpLine, stillSetUp, type SetupHooks } from "./panel";

const challenge = (id: string): Challenge => CHALLENGES.find((c) => c.id === id) as Challenge;
const PORTLAND = loadStream().regions[0];
const OREGON: Region = { ...PORTLAND, region: "oregon", bbox: [-124.6, 41.9, -116.4, 46.3] };
const US: Region = { ...PORTLAND, region: "us-t3", bbox: [-124.8, 24.5, -66.9, 49.4] };
const ROUTE = loadStream().route;
const BRANDS: BrandEntry[] = [
  { id: 4, name: "United States Postal Service", wikidata: "Q668687", locations: 300 },
  { id: 369, name: "Starbucks", wikidata: "Q37158", locations: 250 },
];

function setup(
  regions: Region[] | null = [PORTLAND, OREGON, US],
  settings: Partial<Settings> = {},
) {
  const store = new Store({
    regions,
    region: null,
    pick: null,
    stops: [],
    settings: { ...DEFAULT_SETTINGS, ...settings },
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
    challenge: null,
  });
  const daily = Object.assign(new EventTarget(), {
    phase: "done",
    daily: { route_id: ROUTE.route_id, ratio: 6.6 },
    route: () => Promise.resolve(ROUTE.route_id),
  }) as unknown as DailyRoute;
  const edit = (stops: Stop[]) => store.update({ stops, route: null, run: IDLE_RUN });
  const hooks: SetupHooks = {
    edit: vi.fn(edit),
    open: vi.fn(async () => {
      store.update({ route: ROUTE });
      return true;
    }),
    fit: vi.fn(),
  };
  return { store, daily, hooks, panel: new ChallengePanel(store, daily, hooks) };
}

describe("the challenges listed", () => {
  it("are the served regions', once known, and today's once the server has said", () => {
    expect(setup(null).panel.listed).toEqual([]);
    const regions = (served: Region[]) => setup(served).panel.listed.map(({ region }) => region);
    expect(new Set(regions([PORTLAND]))).toEqual(new Set(["portland", null])); // today's own
    expect(new Set(regions([PORTLAND, OREGON, US]))).toEqual(
      new Set(["portland", "oregon", "us-t3", null]),
    );
    const { panel, daily } = setup([US]);
    expect(panel.listed.map(({ id }) => id)).toEqual(["us-chicago", "daily-worse"]);
    for (const phase of ["planning", "failed"] as const) {
      daily.phase = phase;
      expect(panel.listed.map(({ id }) => id)).toEqual(["us-chicago", "daily-worse"]);
    }
    expect(panel.refusal(challenge("daily-worse"))).toBe("today's route could not be planned");
    for (const phase of ["loading", "none"] as const) {
      daily.phase = phase; // not answered yet; a server without a daily route
      expect(panel.listed.map(({ id }) => id)).toEqual(["us-chicago"]);
    }
  });

  it("refuse a set-up while a run goes, and today's until its route is done", () => {
    const { store, panel, daily } = setup();
    expect(panel.refusal(challenge("portland-bridges"))).toBeNull();
    expect(panel.refusal(challenge("daily-worse"))).toBeNull();
    daily.phase = "planning";
    expect(panel.refusal(challenge("daily-worse"))).toBe("today's route is not planned yet");
    store.updateRun({ phase: "running" });
    expect(panel.refusal(challenge("portland-bridges"))).toBe("a route is being planned or drawn");
    panel.setUp(challenge("portland-bridges"));
    expect(store.state.challenge).toBeNull(); // nothing while busy
  });
});

describe("setting a challenge up", () => {
  it("pins its region and puts in its stops and settings, the rest of those their defaults", () => {
    const { store, panel, hooks } = setup(undefined, {
      seed: 7,
      budgetS: 30,
      turns: "no_left",
      strict: true,
      hard: true,
      target: "ratio",
      tier: "T2",
      reorder: true,
      mpg: 30,
    });
    store.update({ route: ROUTE });
    const bridges = challenge("portland-bridges");
    panel.setUp(bridges);
    expect(hooks.edit).toHaveBeenCalledOnce();
    expect(store.state.stops).toEqual(
      bridges.stops?.map(({ lat, lon }) => ({ lat, lon, snapped: null })),
    );
    expect(store.state.route).toBeNull(); // forgotten, as an edit forgets it
    expect(store.state.settings).toEqual({
      ...DEFAULT_SETTINGS,
      seed: 7, // the seed, the budget and the vehicle are the panel's
      budgetS: 30,
      mpg: 30,
      region: "portland",
      objective: "collect",
      item: "bridge",
    });
    expect(store.state.challenge).toBe("portland-bridges");
    const [[south, west], [north, east]] = [
      [bridges.stops![1].lat, bridges.stops![0].lon],
      [bridges.stops![0].lat, bridges.stops![1].lon],
    ];
    expect(hooks.fit).toHaveBeenCalledWith([
      [west, south],
      [east, north],
    ]);
    expect(setUpLine(bridges, store.state)).toBe("Set up: press Go.");
    expect(setUpLine(challenge("portland-draw"), store.state)).toBe("Set up: press Draw it.");
  });

  it("keeps a trip's own stops for any trip in the region when they lie in it, else none", () => {
    const { store, panel, hooks } = setup();
    const inPortland = [
      { lat: 45.5, lon: -122.6, snapped: null },
      { lat: 45.45, lon: -122.65, snapped: null },
    ];
    store.update({ stops: inPortland });
    panel.setUp(challenge("portland-30x"));
    expect(store.state.stops).toBe(inPortland);
    expect(store.state.settings.region).toBe("portland");
    expect(hooks.fit).toHaveBeenLastCalledWith([
      [PORTLAND.bbox[0], PORTLAND.bbox[1]],
      [PORTLAND.bbox[2], PORTLAND.bbox[3]],
    ]);
    store.update({ stops: [...inPortland, { lat: 41.88, lon: -87.63, snapped: null }] });
    panel.setUp(challenge("portland-hard"));
    expect(store.state.stops).toEqual([]);
    expect(store.state.settings.hard).toBe(true);
    expect(setUpLine(challenge("portland-hard"), store.state)).toBe(
      "Set up: place a start and an end, then press Go.",
    );
  });

  it("picks its brand once the region's brands are known, by wikidata id, else by name", () => {
    const { store, panel } = setup(undefined, { brand: 4 });
    const starbucks = challenge("oregon-starbucks");
    panel.setUp(starbucks);
    expect(store.state.settings.objective).toBe("brand");
    expect(store.state.challenge).toBe("oregon-starbucks"); // set up, the brand still to pick
    store.update({ region: OREGON }); // what the pin makes the region trips run on
    expect(store.state.settings.brand).toBe(4);
    store.update({ brands: BRANDS, settings: { ...store.state.settings, brand: 4 } }); // loadBrands
    expect(store.state.settings.brand).toBe(369);
    expect(store.state.challenge).toBe("oregon-starbucks");
    store.updateSettings({ brand: 4 }); // another brand picked
    expect(store.state.challenge).toBeNull();

    const named = BRANDS.map((brand) => ({ ...brand, wikidata: null })); // already loaded
    const other = setup(undefined, { brand: 4 });
    other.store.update({ region: OREGON, brands: named });
    other.panel.setUp(starbucks);
    expect(other.store.state.settings.brand).toBe(369);
    expect(other.store.state.challenge).toBe("oregon-starbucks");
  });

  it("stays set up through a run and its seed, not past a stop or a fixed setting changed", () => {
    const { store, panel } = setup();
    panel.setUp(challenge("portland-bridges"));
    store.updateSettings({ seed: 882069796, budgetS: 5, units: "km" }); // not the challenge's
    store.update({ run: { ...IDLE_RUN, phase: "running" }, route: null });
    store.update({ run: { ...IDLE_RUN, phase: "done" }, route: ROUTE });
    store.update({ stops: store.state.stops.map((stop) => ({ ...stop, snapped: null })) });
    expect(store.state.challenge).toBe("portland-bridges");
    store.updateSettings({ item: "roundabout" });
    expect(store.state.challenge).toBeNull();
    panel.setUp(challenge("portland-bridges"));
    store.updateSettings({ reorder: true }); // the worst order is the challenge's to set
    expect(store.state.challenge).toBeNull();

    panel.setUp(challenge("portland-bridges"));
    expect(store.state.challenge).toBe("portland-bridges");
    const [start, end] = store.state.stops;
    store.update({ stops: [start, { ...end, lat: end.lat + 0.001 }] });
    expect(store.state.challenge).toBeNull();

    panel.setUp(challenge("portland-draw"));
    store.updateSettings({ leashKm: "auto" });
    expect(store.state.challenge).toBeNull();
    panel.setUp(challenge("portland-draw"));
    store.updateSettings({ region: null });
    expect(store.state.challenge).toBeNull();
  });

  it("opens today's route for today's challenge, set up while that route is shown", async () => {
    const { store, panel, hooks } = setup();
    const worse = challenge("daily-worse");
    panel.setUp(worse);
    await vi.waitFor(() => expect(store.state.challenge).toBe("daily-worse"));
    expect(hooks.open).toHaveBeenCalledWith(ROUTE.route_id);
    expect(hooks.edit).not.toHaveBeenCalled();
    expect(setUpLine(worse, store.state)).toBe("Set up: press Make it worse.");
    const today = { route_id: ROUTE.route_id, ratio: 6.6 } as never;
    expect(stillSetUp(worse, store.state, today, false)).toBe(true);
    store.update({ route: { ...ROUTE, route_id: "0123456789ab" } as RouteDocument });
    expect(store.state.challenge).toBeNull();
  });

  it("sets up a link's challenge, today's once its route is done, if after /regions", async () => {
    const { store, panel, daily, hooks } = setup();
    let answer: (id: string | null) => void = () => undefined;
    daily.phase = "loading"; // /regions has answered, /daily not yet
    daily.route = () => new Promise((resolve) => (answer = resolve));
    expect(panel.listed.map(({ id }) => id)).not.toContain("daily-worse");
    panel.setUpLink("daily-worse");
    await settle();
    expect(hooks.open).not.toHaveBeenCalled();
    daily.phase = "done";
    answer(ROUTE.route_id);
    await vi.waitFor(() => expect(store.state.challenge).toBe("daily-worse"));
    expect(hooks.open).toHaveBeenCalledWith(ROUTE.route_id);

    const failed = setup();
    failed.daily.phase = "failed";
    failed.daily.route = () => Promise.resolve(null); // a day that could not be planned
    failed.panel.setUpLink("daily-worse");
    await settle();
    expect(failed.hooks.open).not.toHaveBeenCalled();
    expect(failed.store.state.challenge).toBeNull();

    const portland = setup([PORTLAND]);
    portland.panel.setUpLink("no-such-challenge");
    portland.panel.setUpLink("oregon-bend"); // a region the server does not serve
    expect(portland.hooks.edit).not.toHaveBeenCalled();
    expect(portland.store.state.challenge).toBeNull();
    portland.panel.setUpLink("portland-bridges");
    expect(portland.store.state.challenge).toBe("portland-bridges");
  });

  it("collapses once a run or a drawing of the user's own starts", () => {
    const { store, panel } = setup();
    expect(panel.collapsed).toBe(false);
    store.updateRun({ phase: "starting" });
    expect(panel.collapsed).toBe(true);
    panel.setCollapsed(false);
    store.updateRun({ phase: "running" });
    expect(panel.collapsed).toBe(false);
    store.updateRun({ phase: "done" });
    store.updateRun({ phase: "drawing" });
    expect(panel.collapsed).toBe(true);
  });
});
