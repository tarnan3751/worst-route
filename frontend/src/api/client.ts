// The API client (docs/reference/api.md): fetch wrappers for the endpoints the app uses (a
// drawing's steps among them), and a job's event stream.
//
// Every call goes to /api, which the dev server proxies to the API (vite.config.ts). A refusal
// is an ApiError carrying the API's body; a refusal without one (a proxy's 502) gets the code
// HTTP_<status>, a success that is not JSON (a page where the API should be) BAD_RESPONSE, and
// a server that cannot be reached NETWORK. `errorBody` is what the banner shows for any error:
// those, or CLIENT_ERROR with the message of an error of the app's own.
//
// The stream is an EventSource: on a dropped connection the browser reconnects by itself and
// sends Last-Event-ID, and the server resumes with a snapshot. The server's `error` event and
// EventSource's own connection error share a name; the first is a MessageEvent with data. The
// stream is closed on `done` and on the server's `error`, since an EventSource would otherwise
// reconnect when the server ends it.
import type {
  Baseline,
  BrandList,
  Daily,
  Done,
  DrawAnswer,
  DrawFinished,
  DrawRequest,
  ErrorBody,
  GeocodeResult,
  JobCreated,
  JobStatus,
  Progress,
  Region,
  RegionPick,
  RouteDocument,
  RouteFeatureCollection,
  RouteRequest,
  ServerConfig,
  Snapped,
  Snapshot,
} from "./types";

export const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly body: ErrorBody;

  constructor(status: number, body: ErrorBody) {
    super(`${body.code}: ${body.message}`);
    this.status = status;
    this.body = body;
  }
}

function isErrorBody(value: unknown): value is ErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as ErrorBody).code === "string" &&
    typeof (value as ErrorBody).message === "string"
  );
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(API_BASE + path, init);
  } catch {
    throw new ApiError(0, {
      code: "NETWORK",
      message: "Cannot reach the server. Try again shortly.",
    });
  }
  const text = await response.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    body = undefined;
  }
  if (!response.ok) {
    const fallback = { code: `HTTP_${response.status}`, message: text || response.statusText };
    throw new ApiError(response.status, isErrorBody(body) ? body : fallback);
  }
  if (body === undefined) {
    throw new ApiError(response.status, { code: "BAD_RESPONSE", message: text.slice(0, 80) });
  }
  return body as T;
}

/** What the banner shows for `error`: the API's body, or CLIENT_ERROR and the error's message. */
export function errorBody(error: unknown): ErrorBody {
  if (error instanceof ApiError) return error.body;
  return { code: "CLIENT_ERROR", message: error instanceof Error ? error.message : String(error) };
}

export function getRegions(): Promise<Region[]> {
  return call("/regions");
}

/** The served region a trip with `stops` runs on (the server picks: the smallest holding them). */
export function pickRegion(stops: { lat: number; lon: number }[]): Promise<RegionPick> {
  return call("/regions/pick", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stops }),
  });
}

/** What the server tells the app: its basemap, whether it is public, its limits. */
export function getConfig(): Promise<ServerConfig> {
  return call("/config");
}

export function createJob(request: RouteRequest): Promise<JobCreated> {
  return call("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export function cancelJob(jobId: string): Promise<JobStatus> {
  return call(`/jobs/${jobId}`, { method: "DELETE" });
}

/** Cancel a job as the page goes away: a request that outlives the page, its answer unread. */
export function cancelJobOnLeave(jobId: string): void {
  void fetch(`${API_BASE}/jobs/${jobId}`, { method: "DELETE", keepalive: true });
}

export function getRoute(routeId: string): Promise<RouteDocument> {
  return call(`/routes/${encodeURIComponent(routeId)}`);
}

export function getRouteGeojson(routeId: string): Promise<RouteFeatureCollection> {
  return call(`/routes/${encodeURIComponent(routeId)}/geojson`);
}

/** The brands of `region`'s build, the most located first. */
export function getBrands(region: string): Promise<BrandList> {
  return call(`/brands?${new URLSearchParams({ region })}`);
}

/** The places `q` may mean in `region`, from the server's geocoding proxy. */
export function geocode(q: string, region: string): Promise<GeocodeResult> {
  return call(`/geocode?${new URLSearchParams({ q, region })}`);
}

/** Today's route: its trip, and how the server's plan of it stands. */
export function getDaily(): Promise<Daily> {
  return call("/daily");
}

/** A route drawn by hand so far, with its legal next moves. */
export function draw(body: DrawRequest): Promise<DrawAnswer> {
  return call("/draw", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Store a drawing that has reached the end as a route. */
export function finishDraw(body: DrawRequest): Promise<DrawFinished> {
  return call("/draw/finish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** What a job's stream calls, in the order the server sends. */
export interface JobHandlers {
  snapped(data: Snapped): void;
  baseline(data: Baseline): void;
  snapshot(data: Snapshot): void;
  progress(data: Progress): void;
  done(data: Done): void;
  error(data: ErrorBody): void;
  /** The connection: open, reconnecting (the browser retries), or lost for good. */
  connection(state: "open" | "reconnecting" | "lost"): void;
}

export interface Subscription {
  close(): void;
}

/** Follow a job's events at `eventsUrl` (the 202's, without the /api prefix) to its end. */
export function subscribe(eventsUrl: string, handlers: JobHandlers): Subscription {
  const source = new EventSource(API_BASE + eventsUrl);
  const data = <T>(event: Event): T => JSON.parse((event as MessageEvent<string>).data) as T;
  source.addEventListener("snapped", (event) => handlers.snapped(data(event)));
  source.addEventListener("baseline", (event) => handlers.baseline(data(event)));
  source.addEventListener("snapshot", (event) => handlers.snapshot(data(event)));
  source.addEventListener("progress", (event) => handlers.progress(data(event)));
  source.addEventListener("done", (event) => {
    source.close();
    handlers.done(data(event));
  });
  source.addEventListener("error", (event) => {
    if (event instanceof MessageEvent) {
      source.close();
      handlers.error(data(event));
    } else {
      handlers.connection(source.readyState === EventSource.CLOSED ? "lost" : "reconnecting");
    }
  });
  source.addEventListener("open", () => handlers.connection("open"));
  return { close: () => source.close() };
}
