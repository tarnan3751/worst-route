// The recorded streams (tests only): jobs planned by `worst-route serve --region portland`
// (build e4b3b4ad90db), as the API sent them. Each holds the regions, the request, the 202,
// every event, the job's status after `done` (its snapshot included), the stored route's
// document, and a refused request. docs/reference/frontend.md, **Tests**, says how to record
// them again.
//
// - `portland-stream`: Powell's → a via in the Central Eastside → Lloyd Center, seed 1, a 6 s
//   budget, as bad as possible, recorded before the API had targets (so without `target` in
//   `done` and the document); a snapshot after the 50th progress; refused: a stop in Seattle.
// - `portland-ratio-stream`: Powell's → Portland State, seed 1, a 4 s budget, a 25× ratio
//   target, reached (`"target"`); refused: a ratio target for a round trip.
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

export const RECORDINGS = ["portland-stream", "portland-ratio-stream"] as const;

/** A recording as it was made; types.test.ts checks it against the types. */
export function loadStream(name: (typeof RECORDINGS)[number] = "portland-stream"): StreamFixture {
  const path = new URL(`./${name}.json`, import.meta.url);
  return JSON.parse(readFileSync(path, "utf8")) as StreamFixture;
}
