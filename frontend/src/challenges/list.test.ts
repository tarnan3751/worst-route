// The challenges' list: ids, once each; regions among those the tests' builds serve (today's
// challenge with none of its own: today's route's); every goal kind present; and each challenge's
// own parts agreeing (an items goal collecting what its settings collect, a pilgrimage naming its
// brand, a drawing on fixed stops).
import { describe, expect, it } from "vitest";
import { MODES } from "../state";
import { CHALLENGES } from "./list";

const SERVED = ["portland", "oregon", "us-t3"]; // the regions the recordings and tests serve
const KINDS = ["ratio", "items", "brand", "alphabet", "drawn", "hard", "daily_worse"];

describe("the challenges", () => {
  it("have ids of lowercase letters, digits and hyphens, each once", () => {
    const ids = CHALLENGES.map(({ id }) => id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[a-z0-9][a-z0-9-]*$/);
  });

  it("are about ten, in the regions served, with every goal kind", () => {
    expect(CHALLENGES.length).toBeGreaterThanOrEqual(8);
    expect(CHALLENGES.length).toBeLessThanOrEqual(12);
    for (const { region, goal } of CHALLENGES) {
      if (goal.kind === "daily_worse") expect(region).toBeNull();
      else expect(SERVED).toContain(region);
    }
    expect(new Set(CHALLENGES.map(({ goal }) => goal.kind))).toEqual(new Set(KINDS));
    for (const region of SERVED) expect(CHALLENGES.some((c) => c.region === region)).toBe(true);
  });

  it("agree with themselves", () => {
    for (const { title, blurb, stops, settings, goal } of CHALLENGES) {
      expect(title.length).toBeGreaterThan(0);
      expect(blurb.length).toBeGreaterThan(0);
      expect(MODES).toContain(settings.objective ?? "distance");
      if (stops !== null) expect(stops.length).toBeGreaterThanOrEqual(2);
      if (goal.kind === "items") {
        expect(settings).toMatchObject({ objective: "collect", item: goal.item });
      }
      if (goal.kind === "brand") {
        expect(settings.objective).toBe("brand");
        expect(settings.brand?.wikidata).toMatch(/^Q\d+$/);
      }
      if (goal.kind === "alphabet") expect(settings.objective).toBe("alphabet");
      if (goal.kind === "hard") expect(settings.hard).toBe(true);
      if (goal.kind === "drawn") expect(stops).not.toBeNull();
      if (goal.kind === "ratio" || goal.kind === "drawn" || goal.kind === "hard") {
        expect(settings.objective ?? "distance").toBe("distance"); // what a drawing plans
      }
    }
  });
});
