import { describe, expect, it } from "vitest";
import { stopErrored } from "./stopList";

describe("the stops an error names", () => {
  const named = (error: { [field: string]: unknown } | null, count: number) =>
    Array.from({ length: count }, (_, k) => k).filter((k) => stopErrored(error, k, count));

  it("are the stop of its stop_index, or every stop along the way on field stops", () => {
    expect(named({ code: "STOP_OUTSIDE_REGION", message: "", stop_index: 1 }, 3)).toEqual([1]);
    expect(named({ code: "NO_NODE_NEAR", message: "", stop_index: null }, 3)).toEqual([]);
    // The alphabet route takes a start and an end only: its refusal names the stops along the way.
    const vias = { code: "INVALID_REQUEST", message: "", field: "stops" };
    expect(named(vias, 4)).toEqual([1, 2]);
    expect(named(vias, 2)).toEqual([]);
    expect(named({ code: "INVALID_REQUEST", message: "", field: "target" }, 4)).toEqual([]);
    expect(named(null, 3)).toEqual([]);
  });
});
