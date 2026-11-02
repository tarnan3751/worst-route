// Setting a challenge up without a DOM: `ChallengePanel` over a real Store, `fetch` stubbed with a
// day's answer and today's route stubbed: today's challenges asked for and listed (none on a server
// without them); Set it up pinning the region, putting in the stops with their towns and the mode,
// constraints and worst order, the brand picked once the region's brands are known; the challenge
// staying set up while what it set is the store's (a run and its seed changing nothing) and no
// longer once a stop or a setting it fixes changes; today's route made worse opening today's route;
// a link's challenge: today's once they come (through a failure and Try again), another day's asked
// for at once, beside today's, and listed first, today's day taken from today's route, else the
// browser's clock, until the server's answer says it, nothing set up once a run has started or a
// route was opened meanwhile, nor for an id it cannot find; a failure asked for again; refusals
// while busy or before today's route is done; the section collapsing when a run starts; the count
// of those done, and the hint under the list.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ANSWER, dayOf } from "../__fixtures__/challenges";
import { json, settle, stubFetch } from "../__fixtures__/http";
import { loadStream } from "../__fixtures__/stream";
import type { BrandEntry, ChallengeList, Region, RouteDocument } from "../api/types";
import type { DailyRoute } from "../daily/panel";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Settings, type Stop } from "../state";
import type { Challenge } from "./list";
import { ChallengePanel, HINT, doneLine, setUpLine, stillSetUp, type SetupHooks } from "./panel";

const US: Region = {
  ...loadStream().regions[0],
  region: "us-t3",
  bbox: [-124.8, 24.5, -66.9, 49.4],
};
const ROUTE = loadStream().route;
const BRANDS: BrandEntry[] = [
  { id: 4, name: "United States Postal Service", wikidata: "Q668687", locations: 300 },
  { id: 1910, name: "McDonald's", wikidata: "Q38076", locations: 250 },
];
const YESTERDAY = dayOf("2026-11-01", false); // a past day: no route made worse

/** The panel over a store, `fetch` answering GET /challenges with `days` by date (today's for
 * none) or `status` for a day it lacks. */
function setup(days: ChallengeList[] = [ANSWER, YESTERDAY], settings: Partial<Settings> = {}) {
  const store = new Store({
    regions: [US],
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
  const calls = stubFetch(({ url }) => {
    const date = new URL(url, "http://app").searchParams.get("date") ?? ANSWER.date;
    const day = days.find((answer) => answer.date === date);
    if (day) return json(day);
    return days.length === 0
      ? json({ code: "NOT_FOUND", message: "no places index" }, 404)
      : json({ code: "INVALID_REQUEST", message: "outside the window", field: "date" }, 422);
  });
  const daily = Object.assign(new EventTarget(), {
    phase: "done",
    daily: { date: ANSWER.date, route_id: ROUTE.route_id, ratio: 6.6 },
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
  return { store, daily, hooks, calls, panel: new ChallengePanel(store, daily, hooks) };
}

/** The panel's challenge of `type`, today's. */
const today = (panel: ChallengePanel, type: string): Challenge =>
  panel.listed.find((c) => c.type === type && c.date === ANSWER.date)!;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the challenges listed", () => {
  it("are today's, asked of the server, once it has answered", async () => {
    const { panel, calls } = setup();
    let listed = 0;
    panel.addEventListener("listed", () => listed++);
    expect(panel.phase).toBe("loading");
    expect(panel.listed).toEqual([]);
    await panel.load();
    expect(calls.map(({ url }) => url)).toEqual(["/api/challenges"]);
    expect(panel.phase).toBe("done");
    expect(panel.listed.map(({ id }) => id)).toEqual(ANSWER.challenges.map(({ id }) => id));
    expect(
      panel.listed.every(({ region, date }) => region === "us-t3" && date === ANSWER.date),
    ).toBe(true);
    expect(listed).toBe(1);
  });

  it("are none on a server without them (404)", async () => {
    const { panel } = setup([]);
    await panel.load();
    expect(panel.phase).toBe("none");
    expect(panel.listed).toEqual([]);
  });

  it("fail when the server cannot answer, and are asked for again", async () => {
    const { panel } = setup();
    const answers = [
      () => Promise.reject(new TypeError("Failed to fetch")),
      () => Promise.resolve(json({ code: "HTTP_502", message: "Bad Gateway" }, 502)),
    ];
    for (const answer of answers) {
      vi.stubGlobal("fetch", answer);
      await panel.load();
      expect(panel.phase).toBe("failed");
      expect(panel.listed).toEqual([]);
    }
    const calls = stubFetch(() => json(ANSWER));
    const phases: string[] = [];
    panel.addEventListener("change", () => phases.push(panel.phase));
    await panel.load(); // Try again
    expect(calls).toHaveLength(1);
    expect(phases[0]).toBe("loading");
    expect(panel.phase).toBe("done");
    expect(panel.listed).toHaveLength(8);
  });

  it("show what comes on a day some types have no trip", async () => {
    const short = { ...ANSWER, challenges: ANSWER.challenges.filter((c) => c.type !== "bridges") };
    const { panel } = setup([short]);
    await panel.load();
    expect(panel.listed.map(({ type }) => type)).not.toContain("bridges");
    expect(panel.listed).toHaveLength(7);
  });

  it("refuse a set-up while a run goes, and today's route made worse until it is done", async () => {
    const { store, panel, daily } = setup();
    await panel.load();
    const worse = today(panel, "daily-worse");
    expect(panel.refusal(today(panel, "bridges"))).toBeNull();
    expect(panel.refusal(worse)).toBeNull();
    daily.phase = "planning";
    expect(panel.refusal(worse)).toBe("Today's route is not planned yet.");
    daily.phase = "failed";
    expect(panel.refusal(worse)).toBe("Today's route could not be planned.");
    store.updateRun({ phase: "running" });
    expect(panel.refusal(today(panel, "bridges"))).toBe("A route is being planned or drawn.");
    panel.setUp(today(panel, "bridges"));
    expect(store.state.challenge).toBeNull(); // nothing while busy
  });
});

describe("setting a challenge up", () => {
  it("pins its region, puts in its stops with their towns and its settings", async () => {
    const { store, panel, hooks } = setup(undefined, {
      seed: 7,
      budgetS: 30,
      turns: "no_right",
      strict: true,
      hard: true,
      target: "ratio",
      tier: "T2",
      leashKm: 40,
      reorder: true,
      certified: true,
      mpg: 30,
    });
    await panel.load();
    store.update({ route: ROUTE });
    const bridges = today(panel, "bridges");
    panel.setUp(bridges);
    expect(hooks.edit).toHaveBeenCalledOnce();
    expect(store.state.stops).toEqual([
      { lat: 40.024, lon: -85.18, label: "Losantville, IN", snapped: null },
      { lat: 39.986, lon: -87.148, label: "Wallace, IN", snapped: null },
    ]);
    expect(store.state.route).toBeNull(); // forgotten, as an edit forgets it
    expect(store.state.settings).toEqual({
      ...DEFAULT_SETTINGS,
      seed: 7, // the seed, the budget and the vehicle are the panel's
      budgetS: 30,
      mpg: 30,
      region: "us-t3",
      objective: "collect",
      item: "bridge",
    });
    expect(store.state.challenge).toBe("bridges-2026-11-02");
    expect(hooks.fit).toHaveBeenCalledWith([
      [-87.148, 39.986],
      [-85.18, 40.024],
    ]);
    expect(setUpLine(bridges)).toBe("Set up. Press Go.");
    expect(setUpLine(today(panel, "daily-worse"))).toBe("Set up. Press Make it worse.");
  });

  it("gives each type its mode, constraints and worst order", async () => {
    const { store, panel } = setup();
    await panel.load();
    const settingsOf = (type: string) => {
      panel.setUp(today(panel, type));
      const { objective, turns, strict, hard, reorder, target } = store.state.settings;
      return { objective, turns, strict, hard, reorder, target, stops: store.state.stops.length };
    };
    const longest = { objective: "distance", turns: "any", strict: false, hard: false };
    const plain = { ...longest, reorder: false, target: "max", stops: 2 };
    expect(settingsOf("long-way")).toEqual(plain);
    expect(settingsOf("slow-way")).toEqual({ ...plain, objective: "time" });
    expect(settingsOf("no-left")).toEqual({ ...plain, turns: "no_left", strict: true });
    expect(settingsOf("hard-mode")).toEqual({ ...plain, hard: true });
    expect(settingsOf("worst-order")).toEqual({ ...plain, reorder: true, stops: 4 });
    expect(settingsOf("pilgrimage")).toEqual({ ...plain, objective: "brand" });
    expect(store.state.stops.map(({ label }) => label)).toEqual(["Losantville, IN", "Wallace, IN"]);
  });

  it("picks its brand once the region's brands are known, by wikidata id, else by name", async () => {
    const { store, panel } = setup(undefined, { brand: 4 });
    await panel.load();
    const pilgrimage = today(panel, "pilgrimage"); // McDonald's, Q38076
    panel.setUp(pilgrimage);
    expect(store.state.settings.objective).toBe("brand");
    expect(store.state.challenge).toBe("pilgrimage-2026-11-02"); // set up, the brand still to pick
    store.update({ region: US }); // what the pin makes the region trips run on
    expect(store.state.settings.brand).toBe(4);
    store.update({ brands: BRANDS, settings: { ...store.state.settings, brand: 4 } }); // loadBrands
    expect(store.state.settings.brand).toBe(1910); // this build's McDonald's, not the answer's id
    expect(store.state.challenge).toBe("pilgrimage-2026-11-02");
    store.updateSettings({ brand: 4 }); // another brand picked
    expect(store.state.challenge).toBeNull();

    const named = BRANDS.map((brand) => ({ ...brand, wikidata: null })); // already loaded
    const other = setup(undefined, { brand: 4 });
    await other.panel.load();
    other.store.update({ region: US, brands: named });
    other.panel.setUp(today(other.panel, "pilgrimage"));
    expect(other.store.state.settings.brand).toBe(1910);
    expect(other.store.state.challenge).toBe("pilgrimage-2026-11-02");
  });

  it("stays set up through a run and its seed, not past a stop or a fixed setting changed", async () => {
    const { store, panel } = setup();
    await panel.load();
    const bridges = today(panel, "bridges");
    panel.setUp(bridges);
    store.updateSettings({ seed: 882069796, budgetS: 5, units: "km" }); // not the challenge's
    store.update({ run: { ...IDLE_RUN, phase: "running" }, route: null });
    store.update({ run: { ...IDLE_RUN, phase: "done" }, route: ROUTE });
    store.update({ stops: store.state.stops.map((stop) => ({ ...stop, snapped: null })) });
    expect(store.state.challenge).toBe("bridges-2026-11-02");
    store.updateSettings({ item: "roundabout" });
    expect(store.state.challenge).toBeNull();
    for (const patch of [{ reorder: true }, { leashKm: 30 }, { tier: "T1" }, { region: null }]) {
      panel.setUp(bridges);
      expect(store.state.challenge).toBe("bridges-2026-11-02");
      store.updateSettings(patch as Partial<Settings>);
      expect(store.state.challenge).toBeNull();
    }
    panel.setUp(bridges);
    const [start, end] = store.state.stops;
    store.update({ stops: [start, { ...end, lat: end.lat + 0.001 }] });
    expect(store.state.challenge).toBeNull();
  });

  it("opens today's route for today's route made worse, set up while it is shown", async () => {
    const { store, panel, hooks } = setup();
    await panel.load();
    const worse = today(panel, "daily-worse");
    panel.setUp(worse);
    await vi.waitFor(() => expect(store.state.challenge).toBe("daily-worse-2026-11-02"));
    expect(hooks.open).toHaveBeenCalledWith(ROUTE.route_id);
    expect(hooks.edit).not.toHaveBeenCalled();
    const shown = { route_id: ROUTE.route_id, ratio: 6.6 } as never;
    expect(stillSetUp(worse, store.state, shown, false)).toBe(true);
    store.update({ route: { ...ROUTE, route_id: "0123456789ab" } as RouteDocument });
    expect(store.state.challenge).toBeNull();
  });
});

describe("a link's challenge", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-02T12:00:00Z")); // the browser's UTC day: today's
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** `fetch` answering today's only once `release` is called (a day's first call is slow), the
   * other days' at once; each of `failures` answers today's call with that status first. */
  function slowToday(...failures: number[]) {
    let release: () => void = () => undefined;
    const today = new Promise<void>((resolve) => (release = resolve));
    const calls = stubFetch(async ({ url }) => {
      if (url.includes("date=2026-11-01")) return json(YESTERDAY);
      if (url.includes("date=2026-11-02")) return json(ANSWER);
      if (url.includes("date=")) return json({ code: "INVALID_REQUEST", message: "" }, 422);
      const status = failures.shift();
      if (status !== undefined) return json({ code: `HTTP_${status}`, message: "" }, status);
      await today;
      return json(ANSWER);
    });
    return { calls, release };
  }

  it("is set up once today's have come", async () => {
    const { store, panel } = setup();
    const { calls, release } = slowToday();
    const loading = panel.load();
    const linked = panel.setUpLink("no-left-2026-11-02");
    await settle();
    expect(store.state.challenge).toBeNull();
    release();
    await loading;
    await linked;
    expect(store.state.challenge).toBe("no-left-2026-11-02");
    expect(store.state.settings.turns).toBe("no_left");
    expect(calls.map(({ url }) => url)).toEqual(["/api/challenges"]); // today's only
  });

  it("of another day is asked for at once, listed first and set up", async () => {
    const { store, panel } = setup();
    const { calls, release } = slowToday();
    const loading = panel.load();
    await panel.setUpLink("hard-mode-2026-11-01"); // today's still to come
    expect(calls.map(({ url }) => url)).toEqual([
      "/api/challenges",
      "/api/challenges?date=2026-11-01",
    ]);
    expect(panel.listed.map(({ id }) => id)).toEqual(["hard-mode-2026-11-01"]);
    expect(store.state.challenge).toBe("hard-mode-2026-11-01");
    expect(store.state.settings.hard).toBe(true);
    release();
    await loading;
    const [first, ...rest] = panel.listed;
    expect(first).toMatchObject({ id: "hard-mode-2026-11-01", date: "2026-11-01" });
    expect(panel.otherDay(first)).toBe(true);
    expect(rest.map(({ id }) => id)).toEqual(ANSWER.challenges.map(({ id }) => id));
    expect(rest.some((c) => panel.otherDay(c))).toBe(false);
    expect(panel.refusal(first)).toBeNull();
    expect(store.state.challenge).toBe("hard-mode-2026-11-01");
  });

  it("of today's waits for today's through a failure and Try again", async () => {
    const { store, panel } = setup();
    const { calls, release } = slowToday(504);
    await panel.load();
    expect(panel.phase).toBe("failed");
    const linked = panel.setUpLink("bridges-2026-11-02");
    await settle();
    expect(panel.listed).toEqual([]); // not another day's
    release();
    await panel.load(); // Try again
    await linked;
    expect(panel.listed.map(({ id }) => id)).toEqual(ANSWER.challenges.map(({ id }) => id));
    expect(panel.listed.some((c) => panel.otherDay(c))).toBe(false);
    expect(store.state.challenge).toBe("bridges-2026-11-02");
    expect(calls.map(({ url }) => url)).toEqual(["/api/challenges", "/api/challenges"]);
  });

  it("of another day is set up while today's fail, and listed first once they come", async () => {
    const { store, panel } = setup();
    const { release } = slowToday(504);
    await panel.load();
    await panel.setUpLink("long-way-2026-11-01");
    expect(panel.listed.map(({ id }) => id)).toEqual(["long-way-2026-11-01"]);
    expect(store.state.challenge).toBe("long-way-2026-11-01");
    release();
    await panel.load(); // Try again
    expect(panel.listed).toHaveLength(9);
    expect(panel.otherDay(panel.listed[0])).toBe(true);
    expect(panel.listed.filter(({ id }) => id === "long-way-2026-11-01")).toHaveLength(1);
  });

  it("takes today's day from today's route, else the browser's clock", async () => {
    vi.setSystemTime(new Date("2026-11-03T00:30:00Z")); // the browser thinks it tomorrow
    const known = setup(); // today's route says 2026-11-02
    const slow = slowToday();
    const loading = known.panel.load();
    const linked = known.panel.setUpLink("no-left-2026-11-02");
    slow.release();
    await loading;
    await linked;
    expect(slow.calls).toHaveLength(1); // today's
    expect(known.store.state.challenge).toBe("no-left-2026-11-02");

    const ahead = setup();
    Object.assign(ahead.daily, { daily: null }); // GET /daily not answered: the browser's day
    const fast = slowToday();
    const aheadLoading = ahead.panel.load();
    await ahead.panel.setUpLink("no-left-2026-11-02"); // asked for by its day at once
    fast.release();
    await aheadLoading;
    expect(fast.calls.map(({ url }) => url)).toEqual([
      "/api/challenges",
      "/api/challenges?date=2026-11-02",
    ]);
    expect(ahead.panel.listed).toHaveLength(8); // today's after all, listed once
    expect(ahead.panel.listed.some((c) => ahead.panel.otherDay(c))).toBe(false);
    expect(ahead.store.state.challenge).toBe("no-left-2026-11-02");

    vi.setSystemTime(new Date("2026-11-01T23:30:00Z")); // and behind
    const behind = setup();
    Object.assign(behind.daily, { daily: null });
    const late = slowToday();
    const behindLoading = behind.panel.load();
    const behindLinked = behind.panel.setUpLink("hard-mode-2026-11-01"); // waits for today's
    late.release();
    await behindLoading;
    await behindLinked;
    expect(late.calls.map(({ url }) => url)).toEqual([
      "/api/challenges",
      "/api/challenges?date=2026-11-01", // after today's, which are 2026-11-02's
    ]);
    expect(behind.panel.listed[0].id).toBe("hard-mode-2026-11-01");
    expect(behind.store.state.challenge).toBe("hard-mode-2026-11-01");
  });

  it("is not set up once a run has started meanwhile, even one that failed", async () => {
    const { store, panel, hooks } = setup();
    const { release } = slowToday();
    const loading = panel.load();
    const linked = panel.setUpLink("long-way-2026-11-02");
    store.updateRun({ phase: "starting" });
    store.updateRun({ phase: "failed" }); // refused: nothing shown, nothing running
    release();
    await loading;
    await linked;
    expect(panel.listed.map(({ id }) => id)).toContain("long-way-2026-11-02");
    expect(hooks.edit).not.toHaveBeenCalled();
    expect(store.state.challenge).toBeNull();
  });

  it("is listed but not set up once a route was opened meanwhile", async () => {
    const { store, panel, hooks } = setup();
    const { release } = slowToday();
    const loading = panel.load();
    const linked = panel.setUpLink("long-way-2026-11-01");
    store.update({ route: ROUTE }); // a route opened meanwhile
    release();
    await loading;
    await linked;
    expect(panel.listed[0].id).toBe("long-way-2026-11-01");
    expect(hooks.edit).not.toHaveBeenCalled();
    expect(store.state.challenge).toBeNull();
  });

  it("sets nothing up for an id it cannot find", async () => {
    const { store, panel, hooks, calls } = setup();
    await panel.load();
    for (const id of ["portland-bridges", "daily-worse", "", "bridges-2026-09-01"]) {
      await panel.setUpLink(id); // no day, the fixed list's of old, a day not answered (422)
    }
    await panel.setUpLink("roundabouts-2026-11-01"); // a type the day lacks
    await panel.setUpLink("alphabet-2026-11-02"); // nor today
    expect(calls.map(({ url }) => url)).toEqual([
      "/api/challenges",
      "/api/challenges?date=2026-09-01",
      "/api/challenges?date=2026-11-01",
    ]);
    expect(panel.phase).toBe("done"); // today's kept whatever another day's answer
    expect(panel.listed).toHaveLength(8);
    expect(hooks.edit).not.toHaveBeenCalled();
    expect(store.state.challenge).toBeNull();
  });
});

describe("the section", () => {
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

  it("counts the challenges done", () => {
    expect(doneLine(1)).toBe("1 challenge done");
    expect(doneLine(2)).toBe("2 challenges done");
    expect(doneLine(1200)).toBe("1,200 challenges done");
  });

  it("says how to play under the list", () => {
    expect(HINT).toBe(
      "Set it up, then Go. Short of the bar? More time, another run, or Make it worse.",
    );
  });
});
