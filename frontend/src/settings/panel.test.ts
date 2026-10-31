import { afterEach, describe, expect, it, vi } from "vitest";
import { json, settle, stubFetch } from "../__fixtures__/http";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
import type { Region } from "../api/types";
import { brandOptions, followBrands, loadBrands, regionOptions } from "./panel";

// The first of /brands?region=portland on build ffe2e7c881ca, as the API orders them.
const BRANDS = [
  { id: 261, name: "United States Postal Service", wikidata: "Q668687", locations: 174 },
  { id: 229, name: "Starbucks", wikidata: "Q37158", locations: 126 },
  { id: 194, name: "Plaid Pantry", wikidata: "Q7200591", locations: 86 },
];

function store(brand: number | null, region: Region | null = null): Store {
  return new Store({
    region,
    stops: [],
    settings: { ...DEFAULT_SETTINGS, brand },
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
    regions: null,
    pick: null,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the brand picker", () => {
  it("lists the region's brands from GET /brands, by name and locations, in the API's order", async () => {
    const calls = stubFetch(() => json({ region: "portland", brands: BRANDS }));
    const s = store(null);
    const brands = await loadBrands(s, "portland");
    expect(calls.map((call) => [call.method, call.url])).toEqual([
      ["GET", "/api/brands?region=portland"],
    ]);
    expect(brandOptions(brands)).toEqual([
      ["261", "United States Postal Service (174)", true],
      ["229", "Starbucks (126)", true],
      ["194", "Plaid Pantry (86)", true],
    ]);
    expect(brandOptions([{ ...BRANDS[0], locations: 1234 }])[0][1]).toBe(
      "United States Postal Service (1,234)",
    );
    expect(s.state.settings.brand).toBe(261); // none was set: the first
    expect(s.state.brands).toEqual(BRANDS); // the picker's, in the store
  });

  it("keeps a brand the region has, and puts the first in place of one it lacks", async () => {
    stubFetch(() => json({ region: "portland", brands: BRANDS }));
    const kept = store(229); // a link's, or a stored route's
    await loadBrands(kept, "portland");
    expect(kept.state.settings.brand).toBe(229);
    const unknown = store(9999);
    await loadBrands(unknown, "portland");
    expect(unknown.state.settings.brand).toBe(261);
  });

  it("leaves the settings alone for a region without brands", async () => {
    stubFetch(() => json({ region: "portland", brands: [] }));
    const s = store(null);
    expect(await loadBrands(s, "portland")).toEqual([]);
    expect(s.state.settings.brand).toBeNull();
    expect(s.state.brands).toEqual([]);
  });

  it("keeps the brand picked when the region changes, by its wikidata id, else its name", async () => {
    // A brand's id is its build's: Starbucks is 229 in Portland's and 12 in another's.
    const other = [
      { id: 300, name: "Dutch Bros", wikidata: "Q5317253", locations: 90 },
      { id: 12, name: "Starbucks Coffee", wikidata: "Q37158", locations: 2210 },
      { id: 229, name: "Burgerville", wikidata: null, locations: 40 },
    ];
    stubFetch(() => json({ region: "oregon", brands: other }));
    const picked = store(229);
    await loadBrands(picked, "oregon", BRANDS);
    expect(picked.state.settings.brand).toBe(12); // Starbucks, not the other build's 229
    const unnamed = [{ id: 5, name: "Burgerville", wikidata: null, locations: 3 }];
    const byName = store(5);
    await loadBrands(byName, "oregon", unnamed);
    expect(byName.state.settings.brand).toBe(229); // the same name
    stubFetch(() => json({ region: "oregon", brands: other.slice(0, 1) }));
    const gone = store(229);
    await loadBrands(gone, "oregon", BRANDS);
    expect(gone.state.settings.brand).toBe(300); // no Starbucks there: the first
  });

  it("drops the brands of a region trips have moved on from", async () => {
    stubFetch(() => json({ region: "portland", brands: BRANDS }));
    const oregon: Region = {
      region: "oregon",
      build_id: "b1",
      graph_version: "g1",
      bbox: [-124.6, 41.5, -116.7, 46.3],
      counts: {},
      tiers: [],
    };
    const moved = store(null, oregon); // the region trips run on is now Oregon
    expect(await loadBrands(moved, "portland")).toEqual(BRANDS);
    expect([moved.state.brands, moved.state.settings.brand]).toEqual([null, null]);
  });
});

describe("the region picker", () => {
  it("offers the server's pick, then each region by its name and roads", () => {
    const tiers = [100, 200, 300, 400, 500].map((edges, t) => ({ tier: `T${t}`, nodes: 0, edges }));
    const oregon: Region = {
      region: "oregon",
      name: "Oregon",
      build_id: "b1",
      graph_version: "g1",
      bbox: [-124.6, 41.5, -116.7, 46.3],
      counts: {},
      tiers,
    };
    const us = {
      ...oregon,
      region: "us-t1",
      name: "United States",
      tiers: tiers.map((t) => ({ ...t, edges: t.tier === "T0" ? 100 : 500 })),
    };
    expect(regionOptions([oregon, us])).toEqual([
      ["", "Picked from the stops", true],
      ["oregon", "Oregon · all roads", true],
      ["us-t1", "United States · primary roads and up", true],
    ]);
  });
});

describe("the brands of the region trips run on", () => {
  const region = (name: string): Region => ({
    region: name,
    build_id: "b1",
    graph_version: "g1",
    bbox: [-124.6, 41.5, -116.7, 46.3],
    counts: {},
    tiers: [],
  });

  it("asks for each region it changes to, and drops a region's answer once it is left", async () => {
    const calls = stubFetch((call) =>
      json({ region: call.url.endsWith("oregon") ? "oregon" : "us-t1", brands: BRANDS }),
    );
    const s = store(null);
    const shown: string[] = [];
    followBrands(s, (text) => shown.push(text));
    s.update({ region: region("oregon") });
    s.update({ region: region("us-t1") }); // before Oregon's answer
    await settle();
    expect(calls.map(({ url }) => url)).toEqual([
      "/api/brands?region=oregon",
      "/api/brands?region=us-t1",
    ]);
    expect(s.state.brands).toEqual(BRANDS);
    s.update({ region: null }); // the stops changed: a pick is pending
    s.update({ region: region("us-t1") }); // the same again: nothing asked
    await settle();
    expect(calls).toHaveLength(2);
    expect(shown).toEqual(["loading…", "loading…"]);
  });

  it("asks again after a failure, even for the same region, so the picker is not left waiting", async () => {
    let fail = true;
    const calls = stubFetch(() =>
      fail
        ? json({ code: "HTTP_502", message: "bad gateway" }, 502)
        : json({ region: "oregon", brands: BRANDS }),
    );
    const s = store(null);
    const shown: string[] = [];
    followBrands(s, (text) => shown.push(text));
    s.update({ region: region("oregon") });
    s.update({ region: null }); // the stops changed while it was asked
    await settle();
    expect(shown).toEqual(["loading…", "brands unavailable"]);
    expect(s.state.error).toEqual({ code: "HTTP_502", message: "bad gateway" });
    fail = false;
    s.update({ region: region("oregon") }); // picked again: the same region
    await settle();
    expect(calls).toHaveLength(2);
    expect(shown.at(-1)).toBe("loading…");
    expect(s.state.brands).toEqual(BRANDS);
  });
});
