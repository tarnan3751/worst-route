import { afterEach, describe, expect, it, vi } from "vitest";
import { json, stubFetch } from "../__fixtures__/http";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
import { brandOptions, loadBrands } from "./panel";

// The first of /brands?region=portland on build ffe2e7c881ca, as the API orders them.
const BRANDS = [
  { id: 261, name: "United States Postal Service", wikidata: "Q668687", locations: 174 },
  { id: 229, name: "Starbucks", wikidata: "Q37158", locations: 126 },
  { id: 194, name: "Plaid Pantry", wikidata: "Q7200591", locations: 86 },
];

function store(brand: number | null): Store {
  return new Store({
    region: null,
    stops: [],
    settings: { ...DEFAULT_SETTINGS, brand },
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
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
});
