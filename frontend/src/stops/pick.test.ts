// The build a trip runs on, without a DOM: `RegionPicker` with `fetch` stubbed and fake timers
// over a real Store, and the stop list's header (`pickLine`). Oregon's and the national T1
// build's tiers are the real builds' (ea3b18096e8b, c4a85b3d36d0).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { json, stubFetch } from "../__fixtures__/http";
import type { Region, RegionPick } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Stop } from "../state";
import {
  PICK_DELAY_MS,
  PICK_RETRIES,
  PICK_RETRY_MS,
  RegionPicker,
  pickLine,
  regionLabel,
  roadsOf,
} from "./pick";

const tiers = (counts: number[]) => counts.map((edges, t) => ({ tier: `T${t}`, nodes: 0, edges }));
const OREGON: Region = {
  region: "oregon",
  name: "Oregon",
  build_id: "ea3b18096e8b",
  graph_version: "dd86410e515c",
  bbox: [-124.5574279, 41.5401763, -116.6646268, 46.2505367],
  counts: {},
  tiers: tiers([14893, 37217, 76003, 159487, 593325]),
};
const US: Region = {
  region: "us-t1",
  name: "United States",
  build_id: "c4a85b3d36d0",
  graph_version: "8846d0d4ab7a",
  bbox: [-124.4976535, 24.5504066, -67.0742873, 49.222996],
  counts: {},
  tiers: tiers([573652, 1324013, 1324013, 1324013, 1324013]),
};
const IN_OREGON: RegionPick = {
  region: "oregon",
  build_id: OREGON.build_id,
  reason: "all stops in Oregon",
};
const ACROSS: RegionPick = {
  region: "us-t1",
  build_id: US.build_id,
  reason: "stops in Oregon and Illinois",
};
const PORTLAND = { lat: 45.5231, lon: -122.6765 };
const BEND = { lat: 44.0582, lon: -121.3153 };
const CHICAGO = { lat: 41.8781, lon: -87.6298 };

function setup(regions: Region[] | null = [OREGON, US]) {
  const store = new Store({
    regions,
    region: null,
    pick: null,
    stops: [],
    settings: DEFAULT_SETTINGS,
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
  });
  new RegionPicker(store);
  const place = (...points: { lat: number; lon: number }[]) =>
    store.update({ stops: points.map((p): Stop => ({ ...p, snapped: null })), error: null });
  return { store, place };
}

/** The server's pick for each request, by the stops' last longitude: east of Oregon is the US. */
const answer = (body: unknown) => {
  const { stops } = body as { stops: { lon: number }[] };
  return json(stops.some(({ lon }) => lon > -116) ? ACROSS : IN_OREGON);
};

/** Let the pending promises run, on the fake clock. */
const settle = () => vi.advanceTimersByTimeAsync(0);

/** Let the delay run out and the answer come. */
async function answered(): Promise<void> {
  await vi.advanceTimersByTimeAsync(PICK_DELAY_MS);
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the build a trip runs on", () => {
  it("names a build by its roads, from its tiers", () => {
    expect(roadsOf(OREGON)).toBe("all roads");
    expect(roadsOf(US)).toBe("primary roads and up");
    expect(roadsOf({ ...OREGON, tiers: [] })).toBe("");
    expect(regionLabel(OREGON)).toBe("Oregon · all roads");
    expect(regionLabel({ ...US, name: undefined })).toBe("us-t1 · primary roads and up");
  });

  it("asks the server once the stops have stopped changing, and plans on its pick", async () => {
    const calls = stubFetch((call) => answer(call.body));
    const { store, place } = setup();
    expect(calls).toHaveLength(0); // no stops, nothing to ask
    expect(pickLine(store.state)).toEqual({ text: "", reason: "" });
    place(PORTLAND);
    place(PORTLAND, BEND);
    expect(store.state.pick).toBe("picking");
    expect(store.state.region).toBeNull(); // so Go waits
    expect(pickLine(store.state).text).toBe("Finding the build…");
    await answered();
    expect(calls.map(({ method, url, body }) => [method, url, body])).toEqual([
      ["POST", "/api/regions/pick", { stops: [PORTLAND, BEND] }], // once, for the last stops
    ]);
    expect(store.state.region).toBe(OREGON);
    expect(store.state.pick).toEqual(IN_OREGON);
    expect(pickLine(store.state)).toEqual({
      text: "Oregon · all roads",
      reason: "all stops in Oregon",
    });
    place(PORTLAND, CHICAGO);
    await answered();
    expect(store.state.region).toBe(US);
    expect(pickLine(store.state)).toEqual({
      text: "United States · primary roads and up",
      reason: "stops in Oregon and Illinois",
    });
  });

  it("drops an answer the stops have moved on from", async () => {
    const slow: ((response: Response) => void)[] = [];
    stubFetch((call) =>
      (call.body as { stops: unknown[] }).stops.length === 2
        ? new Promise((resolve) => slow.push(resolve)) // the first answer, held back
        : answer(call.body),
    );
    const { store, place } = setup();
    place(PORTLAND, CHICAGO);
    await vi.advanceTimersByTimeAsync(PICK_DELAY_MS); // asked; its answer is held
    place(PORTLAND, BEND, PORTLAND);
    await answered();
    expect(store.state.region).toBe(OREGON);
    slow[0](json(ACROSS));
    await settle();
    expect(store.state.region).toBe(OREGON); // the late answer was for other stops
    expect(store.state.pick).toEqual(IN_OREGON);
  });

  it("asks nothing when a job's snaps change no stop's coordinates", async () => {
    const calls = stubFetch((call) => answer(call.body));
    const { store, place } = setup();
    place(PORTLAND, BEND);
    await answered();
    const snapped = { node: 1, lat: 45.5, lon: -122.6, moved_m: 5, note: null };
    store.update({ stops: store.state.stops.map((stop) => ({ ...stop, snapped })) });
    await answered();
    expect(calls).toHaveLength(1);
    expect(store.state.region).toBe(OREGON);
  });

  it("shows a refusal in the banner, naming the stop", async () => {
    const outside = {
      code: "STOP_OUTSIDE_REGION",
      message: "stop 1 (21.3069, -157.8583) is outside every region served here (oregon, us-t1)",
      stop_index: 1,
    };
    stubFetch(() => json(outside, 422));
    const { store, place } = setup();
    place(PORTLAND, { lat: 21.3069, lon: -157.8583 });
    await answered();
    expect(store.state.error).toEqual(outside);
    expect([store.state.region, store.state.pick]).toEqual([null, null]);
    expect(pickLine(store.state).text).toBe("");
  });

  it("takes the region pinned in the settings without asking, and asks once unpinned", async () => {
    const calls = stubFetch((call) => answer(call.body));
    const { store, place } = setup();
    place(PORTLAND, BEND);
    store.updateSettings({ region: "us-t1" });
    expect(store.state.region).toBe(US);
    expect(pickLine(store.state)).toEqual({
      text: "United States · primary roads and up",
      reason: "set in the settings",
    });
    await answered();
    expect(calls).toHaveLength(0); // the pick asked for before the pin was dropped
    store.updateSettings({ region: null });
    await answered();
    expect(calls).toHaveLength(1);
    expect(store.state.region).toBe(OREGON);
  });

  it("waits for /regions, and unpins a region the server does not serve", async () => {
    const calls = stubFetch((call) => answer(call.body));
    const { store, place } = setup(null);
    store.updateSettings({ region: "west-coast" }); // a link's
    place(PORTLAND, BEND);
    await answered();
    expect(calls).toHaveLength(0);
    store.update({ regions: [OREGON, US] });
    expect(store.state.settings.region).toBeNull();
    await answered();
    expect(store.state.region).toBe(OREGON);
  });

  it("asks again after a failure that is not a refusal, then says the server could not be asked", async () => {
    let failures = 2;
    const calls = stubFetch((call) =>
      failures-- > 0 ? json({ code: "HTTP_502", message: "bad gateway" }, 502) : answer(call.body),
    );
    const { store, place } = setup();
    place(PORTLAND, BEND);
    await answered();
    expect(calls).toHaveLength(1);
    expect(store.state.pick).toBe("picking"); // still asking
    await vi.advanceTimersByTimeAsync(PICK_RETRY_MS);
    await vi.advanceTimersByTimeAsync(PICK_RETRY_MS);
    expect(calls).toHaveLength(3);
    expect(store.state.region).toBe(OREGON);
    expect(store.state.error).toBeNull();

    stubFetch(() => json({ code: "HTTP_502", message: "bad gateway" }, 502));
    place(PORTLAND, CHICAGO);
    await answered();
    for (let k = 0; k < PICK_RETRIES; k++) await vi.advanceTimersByTimeAsync(PICK_RETRY_MS);
    expect([store.state.region, store.state.pick]).toEqual([null, null]);
    expect(store.state.error).toEqual({
      code: "HTTP_502",
      message: "could not ask the server which build these stops run on: bad gateway",
    });
    await vi.advanceTimersByTimeAsync(10 * PICK_RETRY_MS); // and asks no more
    const asked = stubFetch((call) => answer(call.body));
    place(PORTLAND, CHICAGO, PORTLAND); // until the stops change
    await answered();
    expect(asked).toHaveLength(1);
    expect(store.state.region).toBe(US);
  });
});
