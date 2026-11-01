import { describe, expect, it } from "vitest";
import { retryOf, sentence } from "./banner";

describe("the banner's message", () => {
  it("is a sentence: a capital first, a full stop last", () => {
    expect(sentence("the server cannot be reached")).toBe("The server cannot be reached.");
    expect(sentence("That stop is outside the map. Try somewhere in the lower 48.")).toBe(
      "That stop is outside the map. Try somewhere in the lower 48.",
    );
    expect(sentence("…, under the strict no-left constraint; try without strict")).toBe(
      "…, under the strict no-left constraint; try without strict.",
    );
  });
});

describe("the banner's retry", () => {
  it("offers to drop strict, or hard mode, when the refusal says it is why", () => {
    const unreachable = "STOP_UNREACHABLE_WITHOUT_REPEAT";
    const leg = "No route from stop 1 to stop 2 without repeating a road";
    for (const [code, message, retry] of [
      [
        "INFEASIBLE_TURN_CONSTRAINT",
        `${leg} and with no left turns. Try without strict.`,
        "strict",
      ],
      [
        "INFEASIBLE_TURN_CONSTRAINT",
        `${leg} and with no road twice and no right turns. Try without strict.`,
        "strict",
      ],
      [
        unreachable,
        `${leg} and with no road twice, after moving stop 2 up to 10 times. Try without it.`,
        "hard",
      ],
      [unreachable, `${leg} and with no road twice. Try without it.`, "hard"],
      [unreachable, `${leg}.`, null],
      [
        "BASE_ROUTE_MISMATCH",
        "the base route comes back to X at edge 4, which hard mode refuses",
        "hard",
      ],
      [
        "BASE_ROUTE_MISMATCH",
        "the base route turns left at node 7, which the strict no-left constraint refuses",
        null,
      ],
      ["INVALID_REQUEST", "strict needs no_left or no_right", null],
    ] as const) {
      expect(retryOf({ code, message })).toBe(retry);
    }
  });
});
