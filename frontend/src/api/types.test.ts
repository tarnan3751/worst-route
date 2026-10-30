// The hand-written types against the API: each type has a runtime spec whose keys the compiler
// holds to the type's (`object<T>` needs exactly T's keys); the specs are checked against the
// shapes docs/reference/api.md writes down (its JSON request example, and the field lists of
// the events, /regions, the 202, the job status, the route document and /geocode) and against
// every payload of the stream recorded from the server.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { RECORDINGS, loadStream } from "../__fixtures__/stream";
import type {
  Baseline,
  DocumentMetrics,
  Done,
  DoneMetrics,
  ErrorBody,
  GeocodeCandidate,
  GeocodeResult,
  JobCreated,
  JobStatus,
  Leg,
  LineString,
  Metrics,
  Progress,
  Region,
  RouteDocument,
  RouteRequest,
  RouteStop,
  Run,
  Snapped,
  SnappedStop,
  Snapshot,
  Splice,
  StopIn,
  Target,
  TargetMetrics,
  TierCount,
  Validation,
  Vehicle,
} from "./types";

// --- Specs ------------------------------------------------------------------------------

type Spec =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "any"
  | { literal: readonly (string | number | boolean)[] }
  | { nullable: Spec }
  | { optional: Spec }
  | { anyOf: Spec[] }
  | { array: Spec }
  | { tuple: Spec[] }
  | { record: Spec }
  | { object: Record<string, Spec>; extra?: Spec };

// A type's keys without its index signature (ErrorBody's error fields, Metrics' counters).
type KnownKeys<T> = keyof {
  [K in keyof T as string extends K ? never : number extends K ? never : K]: T[K];
};

const object = <T>(fields: { [K in KnownKeys<T>]-?: Spec }, extra?: Spec): Spec => ({
  object: fields as Record<string, Spec>,
  extra,
});
const nullable = (spec: Spec): Spec => ({ nullable: spec });
const optional = (spec: Spec): Spec => ({ optional: spec });
const array = (spec: Spec): Spec => ({ array: spec });
const literal = (...values: (string | number | boolean)[]): Spec => ({ literal: values });

/** What is wrong with `value` as `spec`, as paths. */
function problems(value: unknown, spec: Spec, path = "$"): string[] {
  if (spec === "any") return [];
  if (spec === "string" || spec === "number" || spec === "boolean") {
    return typeof value === spec ? [] : [`${path}: ${JSON.stringify(value)} is not a ${spec}`];
  }
  if (spec === "integer") {
    return Number.isInteger(value) ? [] : [`${path}: ${JSON.stringify(value)} is not an integer`];
  }
  if ("literal" in spec) {
    const ok = spec.literal.includes(value as string);
    return ok ? [] : [`${path}: ${JSON.stringify(value)} is not one of ${spec.literal}`];
  }
  if ("nullable" in spec) return value === null ? [] : problems(value, spec.nullable, path);
  if ("optional" in spec) return value === undefined ? [] : problems(value, spec.optional, path);
  if ("anyOf" in spec) {
    const ok = spec.anyOf.some((s) => problems(value, s, path).length === 0);
    return ok ? [] : [`${path}: ${JSON.stringify(value)} matches no alternative`];
  }
  if ("array" in spec || "tuple" in spec) {
    if (!Array.isArray(value)) return [`${path}: not an array`];
    if ("array" in spec) return value.flatMap((v, k) => problems(v, spec.array, `${path}[${k}]`));
    if (value.length !== spec.tuple.length) return [`${path}: not ${spec.tuple.length} long`];
    return value.flatMap((v, k) => problems(v, spec.tuple[k], `${path}[${k}]`));
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return [`${path}: not an object`];
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if ("record" in spec)
    return entries.flatMap(([k, v]) => problems(v, spec.record, `${path}.${k}`));
  const found = entries.flatMap(([k, v]) => {
    const field = spec.object[k] ?? spec.extra;
    return field ? problems(v, field, `${path}.${k}`) : [`${path}.${k}: not in the type`];
  });
  const missing = Object.entries(spec.object)
    .filter(([k, s]) => !(k in (value as object)) && !(typeof s === "object" && "optional" in s))
    .map(([k]) => `${path}.${k}: missing`);
  return [...found, ...missing];
}

const lonLat: Spec = { tuple: ["number", "number"] };
const lineString = object<LineString>({ type: literal("LineString"), coordinates: array(lonLat) });
const tier = literal("auto", "T0", "T1", "T2", "T3", "T4");
const stopReason = literal("budget", "converged", "target", "cancelled", "timeout");
const errorBody = object<ErrorBody>({ code: "string", message: "string" }, "any");
const metrics = object<Metrics>(
  { length_m: "number", time_s: "number", ratio: nullable("number") },
  nullable("number"),
);
const targetMetrics = object<TargetMetrics>({
  kind: literal("ratio", "duration"),
  value: "number",
  achieved: "number",
  within_tolerance: "boolean",
});
// Optional: the max-mode recording predates the targets.
const doneMetrics = object<DoneMetrics>(
  {
    length_m: "number",
    time_s: "number",
    ratio: nullable("number"),
    target: optional(nullable(targetMetrics)),
  },
  nullable("number"),
);

const stopIn = object<StopIn>({
  lat: "number",
  lon: "number",
  label: optional(nullable("string")),
});
const target = object<Target>({
  kind: literal("max", "ratio", "duration"),
  value: optional(nullable("number")),
  seconds: optional(nullable("number")),
});
const vehicle = object<Vehicle>({ mpg: "number", gas_price_usd: "number" });
const routeRequest = object<RouteRequest>({
  region: "string",
  stops: optional(nullable(array(stopIn))),
  objective: optional(literal("distance", "time", "collect", "brand", "alphabet")),
  tier: optional(tier),
  leash_km: optional({ anyOf: ["number", literal("auto")] }),
  budget_s: optional("number"),
  max_iterations: optional("integer"),
  seed: optional(nullable("integer")),
  target: optional(target),
  vehicle: optional(nullable(vehicle)),
  departure: optional(nullable("string")),
  base_route_id: optional(nullable("string")),
});

const region = object<Region>({
  region: "string",
  build_id: "string",
  graph_version: "string",
  bbox: { tuple: ["number", "number", "number", "number"] },
  counts: { record: "integer" },
  tiers: array(object<TierCount>({ tier: "string", nodes: "integer", edges: "integer" })),
});
const jobCreated = object<JobCreated>({ job_id: "string", events_url: "string", seed: "integer" });

const snapped = object<Snapped>({
  stops: array(
    object<SnappedStop>({
      input: object<SnappedStop["input"]>({ lat: "number", lon: "number" }),
      label: nullable("string"),
      node: "integer",
      lat: "number",
      lon: "number",
      moved_m: "number",
      note: nullable("string"),
    }),
  ),
  tier: "string",
  leash_km: "number",
  active_nodes: "integer",
});
const baseline = object<Baseline>({
  length_m: "number",
  time_s: "number",
  geometry: nullable(lineString),
});
const splice = object<Splice>({
  i: "integer",
  j: "integer",
  node_count: "integer",
  coords: array(lonLat),
  node_index: array("integer"),
});
const progress = object<Progress>({
  iteration: "integer",
  elapsed_s: "number",
  score: "number",
  metrics,
  splices: array(splice),
});
const snapshot = object<Snapshot>({
  iteration: "integer",
  elapsed_s: "number",
  node_count: "integer",
  stop_idx: array("integer"),
  geometry: lineString,
  node_index: array("integer"),
  metrics,
});
const done = object<Done>({
  route_id: "string",
  stopped_because: stopReason,
  metrics: doneMetrics,
});

const jobStatus = object<JobStatus>({
  job_id: "string",
  status: literal("queued", "running", "done", "error"),
  region: "string",
  build_id: "string",
  seed: "integer",
  created: "string",
  started: nullable("string"),
  finished: nullable("string"),
  last_event_id: "integer",
  iteration: "integer",
  elapsed_s: "number",
  metrics: nullable(metrics),
  route_id: nullable("string"),
  stopped_because: nullable(stopReason),
  error: nullable(errorBody),
  snapshot: nullable(snapshot),
});

const routeDocument = object<RouteDocument>({
  route_id: "string",
  created: "string",
  graph_version: "string",
  engine_version: "string",
  region: "string",
  build_id: "string",
  seed: "integer",
  request: routeRequest,
  stops: array(
    object<RouteStop>({
      label: nullable("string"),
      node: "integer",
      lat: "number",
      lon: "number",
      moved_m: "number",
      note: nullable("string"),
      route_index: "integer",
    }),
  ),
  geometry: lineString,
  legs: array(
    object<Leg>({
      from_stop: "integer",
      to_stop: "integer",
      node_range: { tuple: ["integer", "integer"] },
    }),
  ),
  metrics: object<DocumentMetrics>({
    length_m: "number",
    baseline_length_m: "number",
    ratio: nullable("number"),
    time_s: "number",
    baseline_time_s: "number",
    time_ratio: nullable("number"),
    departure: nullable("string"),
    arrival: nullable("string"),
    target: optional(nullable(targetMetrics)),
  }),
  run: object<Run>({
    attempts: "integer",
    accepted: "integer",
    stopped_because: stopReason,
    elapsed_s: "number",
    backend: "string",
  }),
  validation: object<Validation>({ ok: "boolean", checked_at: "string" }),
});

const geocodeResult = object<GeocodeResult>({
  query: "string",
  region: "string",
  candidates: array(
    object<GeocodeCandidate>({ label: "string", lat: "number", lon: "number", category: "string" }),
  ),
  cached: "boolean",
});

const EVENTS: Record<string, Spec> = {
  snapped,
  baseline,
  snapshot,
  progress,
  done,
  error: errorBody,
};

// --- The reference's notation -----------------------------------------------------------------

// api.md writes shapes as field lists: `{a, b: {c, d}, e: [{f}], g: text, ...}`. A Fields maps
// each field to its own fields when it has some (an object, or a list of objects), else null.
type Fields = Map<string, Fields | null>;

function parseFields(text: string): Fields {
  let at = 0;
  const space = () => {
    while (/\s/.test(text[at] ?? "")) at++;
  };
  // Skip a value with no fields of its own: up to a comma or a closing bracket at this depth.
  const opaque = () => {
    let depth = 0;
    for (; at < text.length; at++) {
      const c = text[at];
      if (depth === 0 && (c === "," || c === "}" || c === "]")) return;
      if ("([{".includes(c)) depth++;
      if (")]}".includes(c)) depth--;
    }
  };
  const value = (): Fields | null => {
    space();
    if (text[at] === "{") return object();
    if (
      text[at] === "[" &&
      text
        .slice(at + 1)
        .trimStart()
        .startsWith("{")
    ) {
      at = text.indexOf("{", at);
      const item = object();
      space();
      if (text[at] !== "]") throw new Error(`expected ] at ${at} in ${text}`);
      at++;
      return item;
    }
    opaque();
    return null;
  };
  const object = (): Fields => {
    const fields: Fields = new Map();
    at++; // {
    for (;;) {
      space();
      if (text[at] === "}") break;
      const key = /^[A-Za-z_]\w*|^\.\.\./.exec(text.slice(at))?.[0];
      if (!key) throw new Error(`expected a field at ${at} in ${text}`);
      at += key.length;
      space();
      let fieldsOf: Fields | null = null;
      if (text[at] === ":") {
        at++;
        fieldsOf = value();
      }
      if (key !== "...") fields.set(key, fieldsOf);
      space();
      if (text[at] === ",") at++;
    }
    at++; // }
    return fields;
  };
  space();
  const shape = value();
  if (shape === null) throw new Error(`no fields in ${text}`);
  return shape;
}

/** The spec of an object, through nullable, optional and arrays. */
function objectSpec(spec: Spec): { object: Record<string, Spec> } | null {
  if (typeof spec === "string") return null;
  if ("nullable" in spec) return objectSpec(spec.nullable);
  if ("optional" in spec) return objectSpec(spec.optional);
  if ("array" in spec) return objectSpec(spec.array);
  return "object" in spec ? spec : null;
}

/** Where the reference's fields and the spec's differ. */
function differences(fields: Fields, spec: Spec, path = "$"): string[] {
  const object = objectSpec(spec);
  if (object === null) return [`${path}: the reference has fields, the type none`];
  const documented = [...fields.keys()].sort();
  const typed = Object.keys(object.object).sort();
  const out: string[] = [];
  if (documented.join() !== typed.join()) {
    out.push(`${path}: the reference has {${documented}}, the type {${typed}}`);
  }
  for (const [key, inner] of fields) {
    if (inner !== null && key in object.object) {
      out.push(...differences(inner, object.object[key], `${path}.${key}`));
    }
  }
  return out;
}

// --- The tests --------------------------------------------------------------------------------

const reference = readFileSync(new URL("../../../docs/reference/api.md", import.meta.url), "utf8");

/** The `{...}` or `[{...}]` in backticks in the last cell of the table row that starts with
 * `start`: its first, or its `k`th. */
function documentedOn(start: string, k = 0): Fields {
  const line = reference.split("\n").find((l) => l.startsWith(start));
  if (line === undefined) throw new Error(`api.md has no line starting ${start}`);
  const cells = line.split(" | ");
  const shapes = [...cells[cells.length - 1].matchAll(/`(\[?\{[^`]*\}\]?)`/g)];
  return parseFields(shapes[k][1]);
}

describe("the types against docs/reference/api.md", () => {
  it("accepts the reference's request example", () => {
    const example = /```json\n([\s\S]*?)```/.exec(reference)?.[1];
    expect(example).toBeDefined();
    expect(problems(JSON.parse(example!), routeRequest)).toEqual([]);
  });

  it("has every event's fields", () => {
    for (const [name, spec] of Object.entries(EVENTS)) {
      expect(differences(documentedOn(`| \`${name}\` |`), spec, name)).toEqual([]);
    }
    // `done`'s metrics, written after its own fields.
    expect(differences(documentedOn("| `done` |", 1), doneMetrics, "done.metrics")).toEqual([]);
  });

  it("has the fields of /regions, the 202 and the job status", () => {
    expect(differences(documentedOn("| GET | `/regions` |"), region, "regions")).toEqual([]);
    expect(differences(documentedOn("| POST | `/jobs` |"), jobCreated, "202")).toEqual([]);
    const status = /\*\*The job status\*\*[^`]*`[^`]*`[^`]*`(\{[^`]*\})`/.exec(reference)?.[1];
    expect(status).toBeDefined();
    expect(differences(parseFields(status!), jobStatus, "status")).toEqual([]);
  });

  it("has the fields of /geocode", () => {
    expect(differences(documentedOn("| GET | `/geocode` |"), geocodeResult, "geocode")).toEqual([]);
  });

  it("has the route document's fields", () => {
    const block = /\*\*The route document\*\*[^\n]*\n\n```\n([\s\S]*?)```/.exec(reference)?.[1];
    expect(block).toBeDefined();
    expect(differences(parseFields(block!), routeDocument, "route")).toEqual([]);
  });
});

describe.each(RECORDINGS)("the types against the recording %s", (name) => {
  const fixture = loadStream(name);

  it("fit /regions, the request, the 202, the status and the route document", () => {
    expect(problems(fixture.regions, array(region))).toEqual([]);
    expect(problems(fixture.request, routeRequest)).toEqual([]);
    expect(problems(fixture.created, jobCreated)).toEqual([]);
    expect(problems(fixture.status, jobStatus)).toEqual([]);
    expect(problems(fixture.route, routeDocument)).toEqual([]);
  });

  it("fit every event", () => {
    const names = new Set(fixture.events.map((event) => event.event));
    expect([...names].sort()).toEqual(["baseline", "done", "progress", "snapped", "snapshot"]);
    for (const event of fixture.events) {
      expect(problems(event.data, EVENTS[event.event], `${event.event}#${event.id}`)).toEqual([]);
    }
    const ids = fixture.events.map((event) => event.id);
    expect(ids).toEqual(ids.map((_, k) => ids[0] + k)); // a first connection misses none
  });

  it("fit a refusal", () => {
    expect(fixture.refused.status).toBe(422);
    expect(problems(fixture.refused.body, errorBody)).toEqual([]);
  });

  it("catch a payload that does not fit", () => {
    const bad = { ...fixture.created, seed: "1", extra: true };
    expect(problems(bad, jobCreated)).toEqual([
      '$.seed: "1" is not an integer',
      "$.extra: not in the type",
    ]);
    const { job_id: _, ...short } = fixture.created;
    expect(problems(short, jobCreated)).toEqual(["$.job_id: missing"]);
  });
});

describe("what the recordings say", () => {
  it("names the stop to blame for a stop outside the region", () => {
    const { refused } = loadStream("portland-stream");
    expect(refused.body).toMatchObject({ code: "STOP_OUTSIDE_REGION", stop_index: 1 });
  });

  it("reports a ratio target reached, in done and in the document", () => {
    const { events, route, refused } = loadStream("portland-ratio-stream");
    const done = events.at(-1);
    if (done?.event !== "done") throw new Error("no done");
    expect(done.data.stopped_because).toBe("target");
    expect(route.run.stopped_because).toBe("target");
    const target = { kind: "ratio", value: 25, within_tolerance: true };
    expect(done.data.metrics.target).toMatchObject(target);
    expect(route.metrics.target).toMatchObject(target);
    expect(route.request.target).toMatchObject({ kind: "ratio", value: 25 });
    expect(refused.body).toMatchObject({ code: "INVALID_REQUEST", field: "target" });
  });
});
