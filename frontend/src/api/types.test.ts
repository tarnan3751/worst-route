// The hand-written types against the API: each type has a runtime spec whose keys the compiler
// holds to the type's (`object<T>` needs exactly T's keys); the specs are checked against the
// shapes docs/reference/api.md writes down (its JSON request example, and the field lists of
// the events, /regions, the 202, the job status, the route document, its directions, /geocode
// and /config) and against every payload of the streams recorded from the server.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { RECORDINGS, loadStream } from "../__fixtures__/stream";
import type {
  AlphabetLetters,
  Baseline,
  BasemapConfig,
  BrandEntry,
  BrandList,
  BrandVisits,
  Constraints,
  DirectionStep,
  DirectionsResult,
  DocumentMetrics,
  Done,
  DoneMetrics,
  ErrorBody,
  GeocodeCandidate,
  GeocodeResult,
  ItemCounts,
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
  ServerConfig,
  ServerLimits,
  Snapped,
  SnappedStop,
  Snapshot,
  Splice,
  StopIn,
  Target,
  TargetMetrics,
  TierCount,
  Turns,
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
const collectItem = literal("toll_booth", "level_crossing", "roundabout", "bridge");
const itemCounts = object<ItemCounts>({
  toll_booth: "integer",
  level_crossing: "integer",
  roundabout: "integer",
  bridge: "integer",
});
const brandVisits = object<BrandVisits>({
  id: "integer",
  name: "string",
  visits: "integer",
  of: "integer",
});
const alphabetLetters = object<AlphabetLetters>({
  letters: "integer",
  sequence: "string",
  roads: array("string"),
  alpha_from: "integer",
  free_from: "integer",
});
// A collecting route's item and counts, a pilgrimage's brand, an alphabet route's letters;
// absent for every other objective. The delays: optional, the recordings predate them.
const metrics = object<Metrics>(
  {
    length_m: "number",
    time_s: "number",
    delay_s: optional("number"),
    ratio: nullable("number"),
    item: optional(collectItem),
    items: optional(itemCounts),
    brand: optional(brandVisits),
    alphabet: optional(alphabetLetters),
  },
  nullable("number"),
);
const targetMetrics = object<TargetMetrics>({
  kind: literal("ratio", "duration"),
  value: "number",
  achieved: "number",
  within_tolerance: "boolean",
});
const turns = object<Turns>({
  left: "integer",
  right: "integer",
  straight: "integer",
  u_turn: "integer",
});
// Optional: the max-mode recording predates the targets, all of them the delays, the turns
// and the item counts.
const doneMetrics = object<DoneMetrics>(
  {
    length_m: "number",
    time_s: "number",
    ratio: nullable("number"),
    time_ratio: optional(nullable("number")),
    target: optional(nullable(targetMetrics)),
    delay_s: optional("number"),
    turns: optional(turns),
    item: optional(collectItem),
    items: optional(itemCounts),
    brand: optional(brandVisits),
    alphabet: optional(alphabetLetters),
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
const constraints = object<Constraints>({
  no_left: optional("boolean"),
  no_right: optional("boolean"),
  strict: optional("boolean"),
  hard: optional("boolean"),
});
const routeRequest = object<RouteRequest>({
  region: "string",
  stops: optional(nullable(array(stopIn))),
  objective: optional(literal("distance", "time", "collect", "brand", "alphabet")),
  constraints: optional(constraints),
  item: optional(nullable(collectItem)),
  brand: optional(nullable("integer")),
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
  name: optional("string"),
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

const directionStep = object<DirectionStep>({
  step: "integer",
  text: "string",
  distance_m: "number",
  time_s: "number",
  edge_range: { tuple: ["integer", "integer"] },
  kind: literal("depart", "continue", "turn", "ramp", "roundabout", "stop", "arrive", "joke"),
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
    baseline_delay_s: optional("number"), // the recordings predate it
    time_ratio: nullable("number"),
    departure: nullable("string"),
    arrival: nullable("string"),
    target: optional(nullable(targetMetrics)),
    delay_s: optional("number"), // the recordings predate the delays and the turns
    turns: optional(turns),
    items: optional(itemCounts),
    brand: optional(nullable(brandVisits)), // the recordings predate it
    alphabet: optional(nullable(alphabetLetters)), // the recordings predate it
    // the recordings predate the presentation's numbers, directions and toasts
    extra_fuel_gal: optional(nullable("number")),
    extra_cost_usd: optional(nullable("number")),
    states: optional(nullable(array("string"))),
    towns: optional(nullable(array("string"))),
    towns_n: optional(nullable("integer")),
    network_fraction: optional("number"),
  }),
  run: object<Run>({
    attempts: "integer",
    accepted: "integer",
    stopped_because: stopReason,
    elapsed_s: "number",
    backend: "string",
  }),
  validation: object<Validation>({ ok: "boolean", checked_at: "string" }),
  directions: optional(array(directionStep)),
  toasts: optional(array("string")),
});
const directionsResult = object<DirectionsResult>({
  route_id: "string",
  directions: array(directionStep),
});

const geocodeResult = object<GeocodeResult>({
  query: "string",
  region: "string",
  candidates: array(
    object<GeocodeCandidate>({ label: "string", lat: "number", lon: "number", category: "string" }),
  ),
  cached: "boolean",
});

const serverConfig = object<ServerConfig>({
  public: "boolean",
  basemap: object<BasemapConfig>({
    kind: literal("raster", "pmtiles"),
    tiles: nullable("string"),
    glyphs: nullable("string"),
    sprite: nullable("string"),
  }),
  limits: object<ServerLimits>({ budget_max_s: "number", leash_max_km: "number" }),
});

const brandList = object<BrandList>({
  region: "string",
  brands: array(
    object<BrandEntry>({
      id: "integer",
      name: "string",
      wikidata: nullable("string"),
      locations: "integer",
    }),
  ),
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

  it("has the fields of /brands", () => {
    expect(differences(documentedOn("| GET | `/brands` |"), brandList, "brands")).toEqual([]);
    const example = {
      region: "portland",
      brands: [{ id: 1, name: "Starbucks", wikidata: "Q37158", locations: 152 }],
    };
    expect(problems(example, brandList)).toEqual([]);
  });

  it("has the fields of /routes/{id}/directions", () => {
    const row = documentedOn("| GET | `/routes/{id}/directions` |");
    expect(differences(row, directionsResult, "directions")).toEqual([]);
  });

  it("has the fields of /geocode", () => {
    expect(differences(documentedOn("| GET | `/geocode` |"), geocodeResult, "geocode")).toEqual([]);
  });

  it("has the fields of /config", () => {
    expect(differences(documentedOn("| GET | `/config` |"), serverConfig, "config")).toEqual([]);
    const example = {
      public: true,
      basemap: {
        kind: "pmtiles",
        tiles: "/basemap/portland.pmtiles",
        glyphs: "/basemap/fonts/{fontstack}/{range}.pbf",
        sprite: "/basemap/sprites/grayscale",
      },
      limits: { budget_max_s: 60, leash_max_km: 150 },
    };
    expect(problems(example, serverConfig)).toEqual([]);
    expect(problems({ ...example, basemap: { kind: "vector" } }, serverConfig)).not.toEqual([]);
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

  it("reports a slowest-time route's time ratio, delays and turns, in done and the document", () => {
    const { events, route, refused } = loadStream("portland-time-stream");
    const done = events.at(-1);
    if (done?.event !== "done") throw new Error("no done");
    expect(route.request).toMatchObject({ objective: "time", item: null });
    const turns = { left: 277, right: 282, straight: 809, u_turn: 2 };
    for (const metrics of [done.data.metrics, route.metrics]) {
      expect(metrics).toMatchObject({ delay_s: 7140, turns });
      expect(metrics.time_ratio).toBeCloseTo(16.9, 1);
    }
    expect(route.metrics.baseline_delay_s).toBe(520);
    expect(refused.body).toMatchObject({ code: "INVALID_REQUEST", field: "constraints" });
  });

  it("counts a collecting route's bridges in every progress, in done and the document", () => {
    const { events, route, refused } = loadStream("portland-collect-stream");
    const counts = events.flatMap((e) =>
      e.event === "progress" ? [[e.data.metrics.item, e.data.metrics.items?.bridge]] : [],
    );
    expect(counts.every(([item, n]) => item === "bridge" && typeof n === "number")).toBe(true);
    expect([counts[0][1], counts.at(-1)?.[1]]).toEqual([5, 18]);
    const done = events.at(-1);
    if (done?.event !== "done") throw new Error("no done");
    expect(done.data.metrics).toMatchObject({ item: "bridge", items: { bridge: 18 } });
    expect(route.metrics.items).toEqual(done.data.metrics.items);
    expect(route.request).toMatchObject({ objective: "collect", item: "bridge" });
    expect(refused.body).toMatchObject({ code: "INVALID_REQUEST", field: "target" });
  });

  it("carries the vehicle's fuel, the directions and the toasts in the document", () => {
    const { route, refused } = loadStream("portland-directions-stream");
    const { metrics, legs } = route;
    expect(route.request.vehicle).toEqual({ mpg: 24, gas_price_usd: 3.89 });
    expect(metrics.extra_cost_usd).toBeCloseTo(metrics.extra_fuel_gal! * 3.89, 9);
    expect(metrics).toMatchObject({ states: ["Oregon"], towns: ["Portland"], towns_n: 1 });
    const steps = route.directions!;
    expect(steps.map((step) => step.step)).toEqual(steps.map((_, k) => k + 1));
    // The driving steps cover the route's edges in order; the others drive nothing.
    let next = 0;
    for (const {
      edge_range: [a, b],
      kind,
    } of steps) {
      expect(a).toBe(next);
      expect(b > a).toBe(!["stop", "arrive", "joke"].includes(kind));
      next = b;
    }
    expect(next).toBe(legs.at(-1)!.node_range[1]); // the route's edges
    const length = steps.reduce((sum, step) => sum + step.distance_m, 0);
    expect(length).toBeCloseTo(metrics.length_m, 3);
    expect(route.toasts![0]).toBe("14.5× less efficient than the sensible route.");
    expect(refused.body).toMatchObject({ code: "INVALID_REQUEST", field: "vehicle" });
  });

  it("counts a pilgrimage's visits in every progress, in done and the document", () => {
    const { events, route, refused } = loadStream("portland-brand-stream");
    const starbucks = { id: 229, name: "Starbucks", of: 19 };
    const visits = events.flatMap((e) => (e.event === "progress" ? [e.data.metrics.brand] : []));
    expect(visits.every((brand) => expect.objectContaining(starbucks).asymmetricMatch(brand))).toBe(
      true,
    );
    const counts = visits.map((brand) => brand!.visits);
    expect(counts).toEqual([...counts].sort((a, b) => a - b)); // never falling
    expect(counts).toEqual([8, 13, 14]);
    const done = events.at(-1);
    if (done?.event !== "done") throw new Error("no done");
    expect(done.data.metrics.brand).toEqual({ ...starbucks, visits: 14 });
    expect(route.metrics.brand).toEqual(done.data.metrics.brand);
    expect(route.metrics.alphabet).toBeNull();
    expect(route.request).toMatchObject({ objective: "brand", brand: 229, item: null });
    expect(refused.body).toMatchObject({ code: "INVALID_REQUEST", field: "target" });
  });

  it("sends each better alphabet route as a snapshot, then a progress without splices", () => {
    const { events, route, refused } = loadStream("portland-alphabet-stream");
    const better = events.slice(3, -1); // after snapped, baseline and the seed's snapshot
    expect(better.length % 2).toBe(0);
    let score = -Infinity;
    for (let k = 0; k < better.length; k += 2) {
      const [snapshot, progress] = better.slice(k, k + 2);
      if (snapshot.event !== "snapshot" || progress.event !== "progress") {
        throw new Error(`events ${snapshot.id} and ${progress.id}: not a snapshot and a progress`);
      }
      expect(progress.data.splices).toEqual([]);
      expect(progress.data.metrics).toEqual(snapshot.data.metrics);
      expect(progress.data.score).toBeGreaterThan(score); // each one better
      score = progress.data.score;
    }
    const letters = better.flatMap((e) =>
      e.event === "progress" ? [e.data.metrics.alphabet!] : [],
    );
    expect(letters[0]).toMatchObject({ letters: 1, sequence: "A" });
    expect(letters.at(-1)).toMatchObject({ letters: 15, sequence: "ABCDEFGHIJKLMNO" });
    const done = events.at(-1);
    if (done?.event !== "done") throw new Error("no done");
    expect(done.data.metrics.alphabet).toEqual(letters.at(-1));
    expect(route.metrics.alphabet).toEqual(done.data.metrics.alphabet);
    expect(route.metrics.alphabet!.roads[0]).toBe("Southwest Alder Street");
    expect(route.metrics.brand).toBeNull();
    expect(route.request).toMatchObject({ objective: "alphabet", target: { kind: "max" } });
    expect(route.run).toMatchObject({ attempts: 150, accepted: 20 }); // restarts, better routes
    expect(refused.body).toMatchObject({ code: "INVALID_REQUEST", field: "stops" });
  });
});
