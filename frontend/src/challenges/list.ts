// The challenges (docs/reference/frontend.md, **Challenges**): the server's, a day's at a time
// (GET /challenges, docs/reference/api.md), each a trip on the answer's region with the mode and
// settings its setup gives (none for today's route made worse) and a goal measured on a finished
// route (progress.ts grades it; panel.ts sets one up). An id carries its day ("long-way-2026-11-02"),
// so a link names the day to ask for and a badge keeps for good.
import type { ChallengeEntry, ChallengeList } from "../api/types";

/** A challenge of the server's, with the day it is of and the region its setup pins. */
export interface Challenge extends ChallengeEntry {
  date: string; // YYYY-MM-DD, the UTC day
  region: string;
}

/** The challenges of a day's answer. */
export function challengesOf(answer: ChallengeList): Challenge[] {
  return answer.challenges.map((challenge) => ({
    ...challenge,
    date: answer.date,
    region: answer.region,
  }));
}

/** The day an id ends in (YYYY-MM-DD), or null for an id without one. */
export function dateOf(id: string): string | null {
  return /-(\d{4}-\d{2}-\d{2})$/.exec(id)?.[1] ?? null;
}
