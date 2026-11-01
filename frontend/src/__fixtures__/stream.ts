// The recorded streams (tests only): jobs planned by `worst-route serve --region portland`
// (the first two on build e4b3b4ad90db, the next two on 506f747d7458, the next three on
// ffe2e7c881ca, the last on 0b72253e5e8a), as the API sent them. Each
// holds the regions, the request, the 202, every event, the job's status after `done` (its
// snapshot included), the stored route's document, and a refused request.
// docs/reference/frontend.md, **Tests**, says how to record them again.
//
// - `portland-stream`: Powell's → a via in the Central Eastside → Lloyd Center, seed 1, a 6 s
//   budget, as bad as possible, recorded before the API had targets (so without `target` in
//   `done` and the document); a snapshot after the 50th progress; refused: a stop in Seattle.
// - `portland-ratio-stream`: Powell's → Portland State, seed 1, a 4 s budget, a 25× ratio
//   target, reached (`"target"`); refused: a ratio target for a round trip.
// - `portland-time-stream`: Powell's → Reed, seed 1, a 4 s budget, the slowest time; its `done`
//   and document carry the time ratio, the delays and the turns; refused: both turn constraints.
// - `portland-collect-stream`: the same stops, seed 1, 4 s, collecting bridges, counted in every
//   `progress`; refused: a ratio target under collect.
// - `portland-directions-stream`: Powell's → Reed, seed 1, a 0.3 s budget, as bad as possible,
//   with a vehicle (24 mpg, $3.89); its document carries the directions, the toasts, the extra
//   fuel and its cost, the states and towns and the share of the roads; refused: a vehicle of
//   0 mpg.
// - `portland-brand-stream`: Powell's → Reed, seed 1, a 4 s budget cut to 450 attempts, a
//   pilgrimage to Starbucks (brand 229), its visits counted in every `progress`; refused: a ratio
//   target under brand.
// - `portland-alphabet-stream`: Powell's → PDX, seed 1, the alphabet route, 150 restarts; each
//   better route a snapshot, then a progress without splices; refused: a stop along the way.
// - `portland-certified-stream`: NE Alberta Street from 15th to 30th, seed 1, certified with the
//   automatic leash (0.42 km: 182 nodes, 578 edges), proven in 4.1 s; each better route a
//   snapshot, then a progress without splices, every progress with the solver's bound, one alone
//   between them; refused: the area within a 1.5 km leash, too large to certify.
import { readFileSync } from "node:fs";
import type {
  Baseline,
  Done,
  ErrorBody,
  JobCreated,
  JobStatus,
  Progress,
  Region,
  RouteDocument,
  RouteRequest,
  Snapped,
  Snapshot,
} from "../api/types";

export type RecordedEvent = { id: number } & (
  | { event: "snapped"; data: Snapped }
  | { event: "baseline"; data: Baseline }
  | { event: "snapshot"; data: Snapshot }
  | { event: "progress"; data: Progress }
  | { event: "done"; data: Done }
  | { event: "error"; data: ErrorBody }
);

export interface StreamFixture {
  recorded: { server: string; build_id: string; graph_version: string };
  regions: Region[];
  request: RouteRequest;
  created: JobCreated;
  events: RecordedEvent[];
  status: JobStatus;
  route: RouteDocument;
  refused: { request: RouteRequest; status: number; body: ErrorBody };
}

export const RECORDINGS = [
  "portland-stream",
  "portland-ratio-stream",
  "portland-time-stream",
  "portland-collect-stream",
  "portland-directions-stream",
  "portland-brand-stream",
  "portland-alphabet-stream",
  "portland-certified-stream",
] as const;

/** A recording as it was made; types.test.ts checks it against the types. */
export function loadStream(name: (typeof RECORDINGS)[number] = "portland-stream"): StreamFixture {
  const path = new URL(`./${name}.json`, import.meta.url);
  return JSON.parse(readFileSync(path, "utf8")) as StreamFixture;
}
