// The place search without a DOM, as JobRunner's tests drive the run: `fetch` stubbed, a real
// Store, and the stops placed through placeStop, the map click's path. The form's wiring (the
// button and Enter submit, typing only sets the text) is the page's (mountPlaceSearch), seen in
// a browser.
import { afterEach, describe, expect, it, vi } from "vitest";
import { json, stubFetch } from "../__fixtures__/http";
import type { GeocodeCandidate, GeocodeResult, Region } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Stop } from "../state";
import { round6 } from "../util/geo";
import { PlaceSearch, searchRegion } from "./search";
import { placeStop, withStopAdded } from "./stopList";

const REGION: Region = {
  region: "portland",
  build_id: "b1",
  graph_version: "g1",
  bbox: [-123.05, 45.25, -122.35, 45.72],
  counts: {},
  tiers: [],
};
const POWELLS: GeocodeCandidate = {
  label: "Powell's City of Books, 1005, West Burnside Street, Pearl District, Portland",
  lat: 45.5231165,
  lon: -122.6812548,
  category: "shop/books",
};
const HAWTHORNE: GeocodeCandidate = {
  label: "Powell's Books on Hawthorne, 3723, Southeast Hawthorne Boulevard, Portland",
  lat: 45.5120524,
  lon: -122.6207861,
  category: "shop/books",
};
const ANSWER: GeocodeResult = {
  query: "powell's",
  region: "portland",
  candidates: [POWELLS, HAWTHORNE],
  cached: false,
};
const BUSY = {
  code: "RATE_LIMITED",
  message: "the geocoder is busy, try again in a few seconds",
  retry_after_s: 2,
};

function setup(
  stops: Stop[] = [],
  regions: Region[] | null = [REGION],
  settings = DEFAULT_SETTINGS,
) {
  const store = new Store({
    region: null,
    stops,
    settings,
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
    regions,
    pick: null,
  });
  const edit = (next: Stop[]): void => store.update({ stops: next });
  const search = new PlaceSearch(store, (lat, lon) => placeStop(store, edit, lat, lon));
  return { store, search };
}

const stop = (lat: number, lon: number): Stop => ({ lat, lon, snapped: null });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the place search", () => {
  it("asks nothing while typing, and once on Search", async () => {
    const calls = stubFetch(() => json(ANSWER));
    const { search } = setup();
    for (const text of ["P", "Po", "Powell", "Powell's"]) search.setText(text);
    expect(calls).toHaveLength(0);
    await search.search();
    expect(calls).toEqual([
      {
        url: "/api/geocode?q=Powell%27s&region=portland",
        method: "GET",
        body: undefined,
        keepalive: false,
      },
    ]);
  });

  it("sends nothing for a blank text or before /regions has answered", async () => {
    const calls = stubFetch(() => json(ANSWER));
    const { search } = setup();
    search.setText("  \t ");
    expect(search.canSearch).toBe(false);
    await search.search();
    const early = setup([], null).search;
    early.setText("Powell's");
    expect(early.canSearch).toBe(false);
    await early.search();
    expect(calls).toHaveLength(0);
  });

  it("asks in the region pinned, else the served region with the largest bbox", async () => {
    // Not the region the stops so far are picked onto: a stop searched for may lie beyond it.
    const calls = stubFetch(() => json(ANSWER));
    const oregon: Region = { ...REGION, region: "oregon", bbox: [-124.6, 41.5, -116.7, 46.3] };
    const us: Region = { ...REGION, region: "us-t1", bbox: [-124.5, 24.6, -67.1, 49.2] };
    const regions = [REGION, oregon, us];
    expect(searchRegion(setup([], regions).store.state)).toBe(us);
    const pinned = { ...DEFAULT_SETTINGS, region: "oregon" };
    expect(searchRegion(setup([], regions, pinned).store.state)).toBe(oregon);
    expect(searchRegion(setup([], null).store.state)).toBeNull();
    const { search } = setup([stop(45.5, -122.7)], regions);
    search.setText("Chicago");
    await search.search();
    expect(calls.map((call) => call.url)).toEqual(["/api/geocode?q=Chicago&region=us-t1"]);
  });

  it("lists the candidates of an answer, and says when there are none", async () => {
    stubFetch(() => json(ANSWER));
    const { search } = setup();
    search.setText("Powell's");
    const done = search.search();
    expect(search.searching).toBe(true);
    await done;
    expect(search.candidates).toEqual([POWELLS, HAWTHORNE]);
    expect([search.searching, search.searched]).toEqual([false, true]);
    stubFetch(() => json({ ...ANSWER, candidates: [] }));
    await search.search();
    expect([search.candidates, search.searched]).toEqual([[], true]);
  });

  it("places the next stop where a click would, then clears the field", async () => {
    stubFetch(() => json(ANSWER));
    const first = setup();
    first.search.setText("Powell's");
    await first.search.search();
    first.search.pick(0);
    expect(first.store.state.stops).toEqual([stop(45.523117, -122.681255)]);
    expect(first.store.state.stops).toEqual(
      withStopAdded([], round6(POWELLS.lat), round6(POWELLS.lon)),
    );
    expect([first.search.text, first.search.candidates]).toEqual(["", []]);

    // With a start and an end, a pick is a stop along the way, before the end, as a click.
    const ends = [stop(45.5, -122.7), stop(45.48, -122.62)];
    const picked = setup(ends);
    const clicked = setup(ends);
    picked.search.setText("Powell's");
    await picked.search.search();
    picked.search.pick(1);
    placeStop(
      clicked.store,
      (next) => clicked.store.update({ stops: next }),
      HAWTHORNE.lat,
      HAWTHORNE.lon,
    );
    expect(picked.store.state.stops).toEqual(clicked.store.state.stops);
    expect(picked.store.state.stops.map((s) => s.lat)).toEqual([45.5, 45.512052, 45.48]);
  });

  it("places nothing while a job runs, as a click", async () => {
    const calls = stubFetch(() => json(ANSWER));
    const { store, search } = setup();
    search.setText("Powell's");
    await search.search();
    store.updateRun({ phase: "running" });
    search.pick(0);
    expect(store.state.stops).toEqual([]);
    expect(search.candidates).toEqual([POWELLS, HAWTHORNE]); // kept for after the run
    expect(search.canSearch).toBe(false);
    await search.search();
    expect(calls).toHaveLength(1);
  });

  it("shows a 429 in the banner, and a search closes the banner", async () => {
    stubFetch(() => json(BUSY, 429));
    const { store, search } = setup();
    search.setText("Powell's");
    await search.search();
    expect(store.state.error).toEqual(BUSY);
    expect([search.candidates, search.searching, search.searched]).toEqual([[], false, false]);
    stubFetch(() => json(ANSWER));
    await search.search();
    expect(store.state.error).toBeNull();
    expect(search.candidates).toHaveLength(2);
  });
});
