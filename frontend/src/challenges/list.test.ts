// The challenges of a day's answer, each with the answer's day and region, and the day an id ends
// in.
import { describe, expect, it } from "vitest";
import { ANSWER } from "../__fixtures__/challenges";
import { challengesOf, dateOf } from "./list";

describe("the challenges", () => {
  it("are the answer's, in its order, each with its day and region", () => {
    const challenges = challengesOf(ANSWER);
    expect(challenges.map(({ id }) => id)).toEqual(ANSWER.challenges.map(({ id }) => id));
    for (const [k, challenge] of challenges.entries()) {
      expect(challenge).toEqual({ ...ANSWER.challenges[k], date: "2026-11-02", region: "us-t3" });
    }
    expect(challengesOf({ ...ANSWER, challenges: ANSWER.challenges.slice(0, 3) })).toHaveLength(3);
  });

  it("carry their day in their ids", () => {
    for (const { id } of challengesOf(ANSWER)) expect(dateOf(id)).toBe("2026-11-02");
    expect(dateOf("worst-order-2026-10-03")).toBe("2026-10-03");
    for (const id of ["portland-bridges", "daily-worse", "us-chicago", "long-way-2026-11", ""]) {
      expect(dateOf(id)).toBeNull();
    }
    expect(dateOf("long-way-2026-11-02x")).toBeNull();
  });
});
