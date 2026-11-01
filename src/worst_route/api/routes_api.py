"""The endpoints (`docs/reference/api.md`; `docs/PLAN.md` §4).

    GET    /health                     the loaded builds
    GET    /regions                    each region's name, build, bbox, counts and tiers
    POST   /snap                       where a stop would go
    GET    /brands                     the region's brands, for a pilgrimage
    POST   /jobs                       plan a route: 202 {job_id, events_url, seed}
    GET    /jobs/{id}/events           the job's events (SSE; Last-Event-ID resumes)
    GET    /jobs/{id}                  its status and current snapshot (polling)
    DELETE /jobs/{id}                  cancel it; its route so far is stored
    GET    /routes/{id}                a stored route's document, with its directions and toasts
    GET    /routes/{id}/geojson        its FeatureCollection
    POST   /routes/{id}/validate       the validator's report on it

`POST /jobs` refuses before a job exists: a ratio target for a round trip first, then the
objective (an unknown one; the alphabet route to continue from a base route; one whose item,
brand, target, stops or worst order do not fit it: `request.objective_problem`; a brand the build
does not have; a portfolio under the alphabet route or with a base route), a certified route's
settings (`request.certified_problem`: another objective, a target, the worst order, a turn
constraint or hard mode, a round trip; or a portfolio or a base route; all `INVALID_REQUEST` on
`field: "certified"`), the worst order's
stops (more than the region's build takes, `request.reorder_problem`: 422
`TOO_MANY_STOPS_TO_REORDER` with `max_stops`), the budget (at most `Limits.budget_max_s`: 120 s,
or 60 s on a public server; the target's bounds are the body's) and the portfolio (at most
`portfolio_max`: `Limits.portfolio_max`, 4 or 2 on a public server, and never more than the
server's workers: 422 `PORTFOLIO_TOO_LARGE` with `max`), then the job limit (counting a
portfolio's workers; on a public server the client's own too, `limits.client_of`), then for "make it
worse" (`base_route_id`) the base route (its build, engine and stops, in the order it visits them,
tier and leash, which the request may leave out, and `reorder` off; a ratio target for a base round
trip too), then each stop against the region's bbox (`meta.params.bbox`, else the nodes' extent),
then the pre-flight (`jobs.prepare`: a pilgrimage's brand within the request's area, snapping and
the re-snap fallback, a certified route's leash and area, in a worker thread), and for a base
route `plan.check_base` on the pre-flight's stops and masks (`BASE_ROUTE_MISMATCH`). The errors are
`ApiError`s, answered as `ErrorBody`: 422 `INVALID_REQUEST`, `UNKNOWN_REGION`,
`STOP_OUTSIDE_REGION` (+ the stop's `lat` and `lon`, kept out of its sentence, which says where
the server's maps are: `outside_sentence`), `NO_NODE_NEAR`, `STOP_UNREACHABLE_WITHOUT_REPEAT`,
`INFEASIBLE_TURN_CONSTRAINT`, `AREA_TOO_LARGE_TO_CERTIFY` (+ `nodes`, `edges`, `max_nodes`,
`max_edges`, `leash_m`, `field: "leash"`), `TOO_MANY_STOPS_TO_REORDER`, `BUDGET_TOO_LARGE`,
`PORTFOLIO_TOO_LARGE`, `UNKNOWN_ROUTE`, `BUILD_MISMATCH` (also `/validate` on a route another engine
version made), `BASE_ROUTE_MISMATCH`; 429 `TOO_MANY_JOBS` (+ `running`, `queued`: the client's own
jobs, when they are why); 404 `NOT_FOUND`; 409 `BUILD_UNAVAILABLE` (a stored route whose build is no
longer on disk at its graph_version), and `DAILY_JOB` for `DELETE /jobs/{id}` on the daily route's
job while it runs (`daily_api`: the server's own).

A route's document carries its `order` (each stop's position among those asked for; a route
stored before the worst order has the identity), and its request's stops are in the order the
route visits them, with their labels, as its stops and directions are.
A route's document adds what the presentation says about it (`presentation.document.present`:
its directions, its toasts, and its fuel, states, towns and share of the roads), worked out
again on every request from its build, its request, its seed and its region's places index
(`presentation_of`, `places_of`: the region's own, else a served region's whose states hold its
own); `directions_of` gives its directions alone, the same ones, for
`GET /routes/{id}/directions` (`directions_api`). An alphabet route's letters are recounted on
every request too, from the build's edge letters, worked out once per build (`Api.letters`).

`/snap` judges the stop in the tier's nodes and the nodes within `BUBBLE_KM` of it (as a job's
masks would near a stop; `auto` is T4); a job's `snapped` event, which also keeps stops apart
and re-snaps, is the authority.
"""

import secrets
from collections.abc import AsyncIterator, Iterable
from datetime import timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import numpy as np
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sse_starlette import EventSourceResponse, ServerSentEvent

from worst_route.api.jobs import Job, Prepared, TooManyJobs, prepare
from worst_route.api.limits import client_of
from worst_route.api.schemas import (
    BrandOut,
    BrandsOut,
    BuildOut,
    CheckOut,
    DirectionStep,
    DocumentMetrics,
    ErrorBody,
    Health,
    JobCreated,
    JobStatus,
    Leg,
    LineString,
    RegionOut,
    RouteDocument,
    RouteRequestIn,
    RouteStop,
    Run,
    SnapIn,
    SnapOut,
    TierOut,
    Validation,
    ValidationOut,
)
from worst_route.api.sse import server_sent, time_ratio
from worst_route.api.store import StoredRoute
from worst_route.api.worker import RouteArrays, area_fields, unreachable
from worst_route.config import BUBBLE_KM, REGIONS
from worst_route.engine import ENGINE_VERSION
from worst_route.engine.alphabet_search import alphabet_counts, edge_letters
from worst_route.engine.exact import AreaTooLarge
from worst_route.engine.objectives import OBJECTIVE_KEYS
from worst_route.engine.objectives.base import route_delay_s
from worst_route.engine.objectives.brand import BrandRefused, brand_counts, brand_problem
from worst_route.engine.objectives.collect import count_items
from worst_route.engine.plan import BaseRouteMismatch, check_base
from worst_route.engine.request import (
    RouteRequest,
    certified_problem,
    max_reorder_stops,
    objective_problem,
    reorder_problem,
    request_masks,
)
from worst_route.engine.seed_route import resolve_stops
from worst_route.engine.snap import StopUnreachable, is_round_trip, role_of
from worst_route.engine.turns import count_turns
from worst_route.engine.validator import validate_route
from worst_route.graph.data import GraphData
from worst_route.graph.geometry import Geometry
from worst_route.graph.loader import BuildNotFound, Loaded, load_build
from worst_route.graph.spatial import SnapError
from worst_route.graph.store import MissingArray
from worst_route.presentation.directions import Step
from worst_route.presentation.document import present, route_directions
from worst_route.presentation.geojson import feature_collection, line_coordinates
from worst_route.presentation.places import PlaceIndex

if TYPE_CHECKING:
    from worst_route.api.app import Api

router = APIRouter()


class ApiError(Exception):
    """A refusal: the HTTP status, the error code, a sentence, and the error's fields."""

    def __init__(self, status: int, code: str, message: str, **fields: Any) -> None:
        super().__init__(message)
        self.status = status
        self.body = ErrorBody(code=code, message=message, **fields)


def api_of(request: Request) -> "Api":
    """The application's state."""
    api: Api = request.app.state.api
    return api


def portfolio_max(api: "Api") -> int:
    """The most seeds a job may run here: the server's limit (`Limits.portfolio_max`), and
    never more than its workers."""
    return min(api.limits.portfolio_max, api.jobs.settings.max_jobs)


def region_of(api: "Api", region: str) -> Loaded:
    """The build served for `region`, else UNKNOWN_REGION."""
    loaded = api.builds.get(region)
    if loaded is None:
        known = ", ".join(sorted(api.builds))
        raise ApiError(422, "UNKNOWN_REGION", f"region {region!r} is not served here ({known})")
    return loaded


def bbox_of(loaded: Loaded) -> tuple[float, float, float, float]:
    """The region's bbox: the build's own (`params.bbox`), else its nodes' extent (`Loaded.bbox`,
    worked out once a build)."""
    return loaded.bbox


def holds(loaded: Loaded, lat: float, lon: float) -> bool:
    """Whether the region's bbox (`bbox_of`) holds (lat, lon)."""
    west, south, east, north = bbox_of(loaded)
    return west <= lon <= east and south <= lat <= north


def display_of(region: str) -> str:
    """What the frontend calls a region (`config.Region.display`); its id when config does not
    name it."""
    known = REGIONS.get(region)
    return region if known is None else known.display


def outside_sentence(served: Iterable[str]) -> str:
    """STOP_OUTSIDE_REGION's sentence, for the person who placed the stop, by the regions served:
    the lower 48 when one of them holds every contiguous state (`us`'s), else their names
    ("Oregon or Portland"). The stop's coordinates are the error's fields, not the sentence's."""
    contiguous = set(REGIONS["us"].states)
    known = [REGIONS[region] for region in served if region in REGIONS]
    if any(contiguous <= set(region.states) for region in known):
        where = "the lower 48"
    else:
        names = list(dict.fromkeys(display_of(region) for region in served))
        where = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
    return f"That stop is outside the map. Try somewhere in {where}."


def check_inside(api: "Api", loaded: Loaded, lat: float, lon: float, **fields: Any) -> None:
    """STOP_OUTSIDE_REGION, with (lat, lon) and `fields`, unless the region's bbox holds it."""
    if not holds(loaded, lat, lon):
        message = outside_sentence(api.builds)
        raise ApiError(422, "STOP_OUTSIDE_REGION", message, lat=lat, lon=lon, **fields)


def _job(api: "Api", job_id: str) -> Job:
    job = api.jobs.jobs.get(job_id)
    if job is None:
        raise ApiError(404, "NOT_FOUND", f"no job {job_id} (jobs are kept an hour after they end)")
    return job


def stored_route(api: "Api", route_id: str) -> StoredRoute:
    """The route stored as `route_id`, else NOT_FOUND."""
    stored = api.store.get(route_id)
    if stored is None:
        raise ApiError(404, "NOT_FOUND", f"no route {route_id}")
    return stored


def build_of(api: "Api", stored: StoredRoute) -> Loaded:
    """The build a stored route was planned on, at its graph_version: the served one when it is
    served, else read from disk (`load_build` keeps the two used last)."""
    served = api.builds.get(stored.region)
    if served is not None and served.data.meta["build_id"] == stored.build_id:
        loaded = served
    else:
        try:
            loaded = load_build(
                api.settings.data_dir, stored.region, stored.build_id, backend=api.settings.backend
            )
        except BuildNotFound as error:
            raise ApiError(409, "BUILD_UNAVAILABLE", str(error)) from None
    if loaded.data.meta["graph_version"] != stored.graph_version:
        message = (
            f"build {stored.region}/{stored.build_id} is graph_version "
            f"{loaded.data.meta['graph_version'][:12]}, the route was planned on "
            f"{stored.graph_version[:12]}"
        )
        raise ApiError(409, "BUILD_UNAVAILABLE", message)
    return loaded


def places_of(api: "Api", region: str) -> PlaceIndex | None:
    """The places index for a route on `region`: the region's own when it is served with one,
    else that of the served region with the fewest states among those whose states hold all of
    its own (`config.Region.fips`; the first served on a tie), never one clipped to a bbox, whose
    index holds only its bbox's towns; None when none does."""
    own, known = api.places.get(region), REGIONS.get(region)
    if own is not None or known is None:
        return own
    covering = [
        (len(other.fips), index)
        for name, index in api.places.items()
        if index is not None
        and (other := REGIONS.get(name)) is not None
        and other.bbox is None
        and known.fips <= other.fips
    ]
    return min(covering, key=lambda c: c[0])[1] if covering else None


def geometry_of(loaded: Loaded) -> Geometry:
    """The build's geometry, to draw a stored route; BUILD_UNAVAILABLE when its files are gone."""
    try:
        return loaded.geometry
    except MissingArray as error:
        raise ApiError(409, "BUILD_UNAVAILABLE", str(error)) from None


def stored_delays(data: GraphData, stored: StoredRoute) -> tuple[float, float]:
    """The delays (signals, stop signs) the route and its baseline meet, from the build: the
    baseline's along its stored edges, so a route stored before the documents carried them has
    them too."""
    first = np.array([stored.stops[0].node], dtype=np.int32)
    baseline_nodes = np.concatenate((first, data.edge_v[stored.baseline_edges]))
    return route_delay_s(data, stored.nodes), route_delay_s(data, baseline_nodes)


@router.get("/health")
def health(request: Request) -> Health:
    """Liveness and the loaded builds."""
    api = api_of(request)
    builds = [
        BuildOut(
            region=region,
            build_id=loaded.data.meta["build_id"],
            graph_version=loaded.data.meta["graph_version"],
        )
        for region, loaded in api.builds.items()
    ]
    return Health(builds=builds)


@router.get("/regions")
def regions(request: Request) -> list[RegionOut]:
    """Each region's name, build, bbox, counts and tiers, in the order served; a region
    `config.REGIONS` does not name is named by its id."""
    out = []
    for region, loaded in api_of(request).builds.items():
        data = loaded.data
        tiers = [
            TierOut(
                tier=f"T{t}",
                nodes=int(data.tier_node_mask[t].sum()),
                edges=int(data.tier_edge_mask[t].sum()),
            )
            for t in range(len(data.tier_node_mask))
        ]
        out.append(
            RegionOut(
                region=region,
                name=display_of(region),
                build_id=data.meta["build_id"],
                graph_version=data.meta["graph_version"],
                bbox=bbox_of(loaded),
                counts=data.meta["counts"],
                tiers=tiers,
            )
        )
    return out


@router.post("/snap")
def snap(body: SnapIn, request: Request) -> SnapOut:
    """Where a stop at (lat, lon) would go, in its role."""
    api = api_of(request)
    loaded = region_of(api, body.region)
    check_inside(api, loaded, body.lat, body.lon)
    data, spatial = loaded.data, loaded.spatial
    tier = 4 if body.tier == "auto" else int(body.tier[1])
    xy = np.array([spatial.project(body.lat, body.lon)])
    mask = data.tier_node_mask[tier] | spatial.bubble_mask(xy, BUBBLE_KM * 1000)
    try:
        s = spatial.snap(body.lat, body.lon, body.role, mask)
    except SnapError as error:
        raise ApiError(422, "NO_NODE_NEAR", str(error)) from None
    return SnapOut(node=s.node, lat=s.lat, lon=s.lon, moved_m=s.moved_m, note=s.note)


@router.post("/jobs", status_code=202)
async def create_job(body: RouteRequestIn, request: Request) -> JobCreated:
    """Start planning a route; its events are at `events_url`."""
    api = api_of(request)
    if body.stops is not None:
        _check_round_trip(body, [(s.lat, s.lon) for s in body.stops])
    loaded = region_of(api, body.region)
    if body.objective not in OBJECTIVE_KEYS:
        message = f"unknown objective {body.objective!r}; known: {', '.join(OBJECTIVE_KEYS)}"
        raise ApiError(422, "INVALID_REQUEST", message)
    if body.objective == "alphabet" and body.base_route_id is not None:
        message = "the alphabet route cannot be continued"
        raise ApiError(422, "INVALID_REQUEST", message, field="base_route_id")
    count = len(body.stops) if body.stops is not None else 2
    problem = objective_problem(
        body.objective, body.item, body.target.kind, body.brand, count, body.reorder
    )
    if problem is not None:
        raise ApiError(422, "INVALID_REQUEST", problem[1], field=problem[0])
    if body.brand is not None and (unknown := brand_problem(loaded.data, body.brand)) is not None:
        message = f"{unknown}; GET /brands?region={body.region} lists the brands"
        raise ApiError(422, "INVALID_REQUEST", message, field="brand")
    if body.portfolio > 1 and body.objective == "alphabet":
        message = "the alphabet route runs one search: no portfolio"
        raise ApiError(422, "INVALID_REQUEST", message, field="portfolio")
    if body.portfolio > 1 and body.base_route_id is not None:
        message = "a continuation runs one search: no portfolio"
        raise ApiError(422, "INVALID_REQUEST", message, field="portfolio")
    if body.certified:
        _check_certified(body)
    reordering = body.reorder and body.base_route_id is None  # a continuation keeps its order
    if reordering and (too_many := reorder_problem(count, loaded.data)) is not None:
        cap = max_reorder_stops(loaded.data)
        raise ApiError(422, "TOO_MANY_STOPS_TO_REORDER", too_many, max_stops=cap)
    budget_max_s = api.limits.budget_max_s
    if body.budget_s > budget_max_s:
        message = f"The time budget is at most {budget_max_s:g} s."
        raise ApiError(422, "BUDGET_TOO_LARGE", message, max_s=budget_max_s)
    if body.portfolio > (most := portfolio_max(api)):
        message = f"At most {most} runs at once on this server."
        raise ApiError(422, "PORTFOLIO_TOO_LARGE", message, max=most)
    client = client_of(request.scope, api.settings.trust_proxy)
    refused = api.jobs.refusal(client, body.portfolio)
    if refused is not None:
        raise _too_many(refused)
    seed = body.seed if body.seed is not None else secrets.randbelow(2**31)
    base: StoredRoute | None = None
    planned = body.model_copy(update={"seed": seed})
    if body.base_route_id is not None:
        base = await run_in_threadpool(api.store.get, body.base_route_id)
        if base is None:
            message = f"no route {body.base_route_id} to continue"
            raise ApiError(422, "UNKNOWN_ROUTE", message, base_route_id=body.base_route_id)
        planned = _continuing(loaded, body, base, seed)
    stops = planned.stops or []
    for k, stop in enumerate(stops):
        check_inside(api, loaded, stop.lat, stop.lon, stop_index=k)
    try:
        engine_request = planned.engine_request(seed)
    except ValidationError as error:
        raise ApiError(422, "INVALID_REQUEST", str(error)) from None
    prepared = await run_in_threadpool(preflight, loaded, engine_request)
    arrays = None
    if base is not None:
        try:
            check_base(
                base,
                prepared.masks,
                [s.node for s in prepared.snaps],
                data=loaded.data,
                constraints=engine_request.constraints,
            )
        except BaseRouteMismatch as error:
            raise ApiError(422, "BASE_ROUTE_MISMATCH", str(error), field=error.field) from None
        arrays = RouteArrays(base.nodes, base.edges, base.stop_idx, base.metrics)
    asked = planned.model_dump(mode="json")
    try:
        job = api.jobs.submit(
            loaded, prepared, input=asked, base=arrays, client=client, portfolio=body.portfolio
        )
    except TooManyJobs as full:
        raise _too_many(full) from None
    return JobCreated(job_id=job.id, events_url=f"/jobs/{job.id}/events", seed=seed)


def _check_certified(body: RouteRequestIn) -> None:
    """Refuse what a certified route does not take, on `field: "certified"`."""
    stops = [(s.lat, s.lon) for s in body.stops or []]
    problem = certified_problem(
        body.objective, body.target.kind, body.reorder, body.constraints, stops
    )
    if problem is None and body.portfolio > 1:
        problem = "a certified route runs one search: no portfolio"
    if problem is None and body.base_route_id is not None:
        problem = "a certified route is planned from its stops: no base route"
    if problem is not None:
        raise ApiError(422, "INVALID_REQUEST", problem, field="certified")


def _too_many(refused: TooManyJobs) -> ApiError:
    """TOO_MANY_JOBS: the server's, or the client's own, with its counts."""
    if refused.running is None:
        return ApiError(429, "TOO_MANY_JOBS", "The server is busy. Try again in a minute.")
    message = "You already have a route planning. Wait for it to finish."
    return ApiError(429, "TOO_MANY_JOBS", message, running=refused.running, queued=refused.queued)


def _continuing(
    loaded: Loaded, body: RouteRequestIn, base: StoredRoute, seed: int
) -> RouteRequestIn:
    """The request "make it worse" plans: the base route's stops (in the order it visits them,
    and its stored input's labels with them), tier and leash, and no worst order."""
    version = loaded.data.meta["graph_version"]
    if base.region != body.region or base.graph_version != version:
        message = (
            f"route {base.route_id} was planned on {base.region} graph_version "
            f"{base.graph_version[:12]}; this server has {body.region} {version[:12]}"
        )
        raise ApiError(422, "BUILD_MISMATCH", message)
    _check_engine(base)
    _check_round_trip(body, base.request.stops)
    given = body.model_fields_set
    if body.stops is not None and [(s.lat, s.lon) for s in body.stops] != base.request.stops:
        raise ApiError(422, "BASE_ROUTE_MISMATCH", "the stops differ", field="stops")
    for name in ("tier", "leash_km"):
        if name in given and getattr(body, name) != getattr(base.request, name):
            raise ApiError(422, "BASE_ROUTE_MISMATCH", f"the {name} differs", field=name)
    values = body.model_dump()
    values.update(
        stops=base.input["stops"],
        tier=base.request.tier,
        leash_km=base.request.leash_km,
        seed=seed,
        reorder=False,
    )
    return RouteRequestIn.model_validate(values)


def _check_round_trip(body: RouteRequestIn, stops: list[tuple[float, float]]) -> None:
    """Refuse a ratio target for a round trip, whose baseline is 0 m."""
    if body.target.kind == "ratio" and is_round_trip(stops):
        message = "A round trip has no ratio to aim for. Set the goal to a drive time."
        raise ApiError(422, "INVALID_REQUEST", message, field="target")


def _check_engine(stored: StoredRoute) -> None:
    """Refuse a route another engine version made: its stops could be re-derived differently."""
    if stored.engine_version != ENGINE_VERSION:
        message = (
            f"route {stored.route_id} was made by engine version {stored.engine_version}; "
            f"this server runs {ENGINE_VERSION}: re-plan"
        )
        raise ApiError(422, "BUILD_MISMATCH", message)


def preflight(loaded: Loaded, request: RouteRequest) -> Prepared:
    """`jobs.prepare`, its errors as `POST /jobs` answers them (`POST /draw` too)."""
    try:
        return prepare(loaded, request)
    except BrandRefused as error:
        raise ApiError(422, "INVALID_REQUEST", str(error), field=error.field) from None
    except SnapError as error:
        stop_index = _unsnappable(loaded, request)
        raise ApiError(422, "NO_NODE_NEAR", str(error), stop_index=stop_index) from None
    except StopUnreachable as error:
        code, message, fields = unreachable(error)
        raise ApiError(422, code, message, **fields) from None
    except AreaTooLarge as error:
        raise ApiError(422, "AREA_TOO_LARGE_TO_CERTIFY", str(error), **area_fields(error)) from None


def _unsnappable(loaded: Loaded, request: RouteRequest) -> int | None:
    """The first stop that no node near it can play alone, if one is to blame."""
    masks = request_masks(loaded.spatial, loaded.data, request)
    for k, (lat, lon) in enumerate(request.stops):
        try:
            loaded.spatial.snap(lat, lon, role_of(k, len(request.stops)), masks.active)
        except SnapError:
            return k
    return None


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> EventSourceResponse:
    """The job's events as Server-Sent Events, resumed after `Last-Event-ID`."""
    api = api_of(request)
    job = _job(api, job_id)
    last = request.headers.get("last-event-id", "")

    async def stream() -> AsyncIterator[ServerSentEvent]:
        # Subscribed only once the stream runs, so a client gone before it starts leaves none.
        subscription = api.jobs.subscribe(job, int(last) if last.isdigit() else None)
        try:
            async for event in subscription.events():
                yield server_sent(event)
        finally:
            subscription.close()

    return EventSourceResponse(stream())


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, request: Request) -> JobStatus:
    """The job's status and its route's current snapshot (on the event loop, which owns the
    jobs)."""
    api = api_of(request)
    return api.jobs.status(_job(api, job_id), with_snapshot=True)


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str, request: Request) -> JobStatus:
    """Cancel the job; its route so far is stored and reported in `done`. Not the daily
    route's, which is the server's."""
    api = api_of(request)
    job = _job(api, job_id)
    if job.id in api.daily_jobs:
        message = f"job {job.id} plans today's route for everyone (GET /daily); it is not cancelled"
        raise ApiError(409, "DAILY_JOB", message)
    await api.jobs.cancel(job)
    return api.jobs.status(job, with_snapshot=False)


@router.get("/routes/{route_id}")
def route(route_id: str, request: Request) -> RouteDocument:
    """A stored route's document, with its directions and toasts."""
    api = api_of(request)
    stored = stored_route(api, route_id)
    loaded = build_of(api, stored)
    data = loaded.data
    asked = RouteRequestIn.model_validate(stored.input)
    labels = [s.label for s in asked.stops or []]
    m = stored.metrics_json
    time_s, baseline_time_s = float(m["time_s"]), float(m["baseline_time_s"])
    delay_s, baseline_delay_s = stored_delays(data, stored)
    departure = asked.departure
    arrival = departure + timedelta(seconds=time_s + delay_s) if departure else None
    metrics: dict[str, Any] = {
        "length_m": m["length_m"],
        "baseline_length_m": m["baseline_length_m"],
        "ratio": m["ratio"],
        "time_s": time_s,
        "baseline_time_s": baseline_time_s,
        "baseline_delay_s": baseline_delay_s,
        "time_ratio": time_ratio(time_s, delay_s, baseline_time_s, baseline_delay_s),
        "departure": departure,
        "arrival": arrival,
        "target": m.get("target"),
        "delay_s": delay_s,
        "turns": count_turns(data, stored.edges),
        "items": count_items(data, stored.nodes, stored.edges),
        "brand": _brand_visits(loaded, stored),
        "alphabet": _alphabet_letters(api, data, stored),
    }
    presented = presentation_of(api, loaded, stored, asked, metrics)
    return RouteDocument(
        route_id=stored.route_id,
        created=stored.created,
        graph_version=stored.graph_version,
        engine_version=stored.engine_version,
        region=stored.region,
        build_id=stored.build_id,
        seed=stored.seed,
        request=asked,
        stops=[
            RouteStop(
                label=label,
                node=s.node,
                lat=s.lat,
                lon=s.lon,
                moved_m=s.moved_m,
                note=s.note,
                route_index=at,
            )
            for label, s, at in zip(labels, stored.stops, stored.stop_idx, strict=True)
        ],
        order=list(stored.order),
        geometry=LineString(coordinates=line_coordinates(data, geometry_of(loaded), stored.edges)),
        legs=[
            Leg(from_stop=k, to_stop=k + 1, node_range=(a, b))
            for k, (a, b) in enumerate(pairwise(stored.stop_idx))
        ],
        metrics=DocumentMetrics(**metrics, **presented["metrics_extra"]),
        run=Run(**stored.run),
        validation=Validation(ok=True, checked_at=stored.created),
        directions=[DirectionStep(**step) for step in presented["directions"]],
        toasts=presented["toasts"],
    )


def presentation_of(
    api: "Api", loaded: Loaded, stored: StoredRoute, asked: RouteRequestIn, metrics: dict[str, Any]
) -> dict[str, Any]:
    """What the presentation adds to a stored route's document (`present`), from its request's
    masks, labels and vehicle, its seed and its region's places index (`places_of`)."""
    masks = request_masks(loaded.spatial, loaded.data, stored.request)
    places = places_of(api, stored.region)
    return present(
        loaded.data,
        stored.nodes,
        stored.edges,
        stored.stop_idx,
        metrics,
        active=masks.active,
        banned=masks.banned,
        seed=stored.seed,
        labels=[s.label for s in asked.stops or []],
        vehicle=asked.vehicle,
        places=places,
        geometry=geometry_of(loaded) if places is not None else None,
    )


def directions_of(data: GraphData, stored: StoredRoute) -> list[Step]:
    """A stored route's directions, the ones its document has."""
    asked = RouteRequestIn.model_validate(stored.input)
    return route_directions(
        data,
        stored.nodes,
        stored.edges,
        stored.stop_idx,
        labels=[s.label for s in asked.stops or []],
        seed=stored.seed,
        ratio=stored.metrics_json["ratio"],
    )


def _brand_visits(loaded: Loaded, stored: StoredRoute) -> dict[str, Any] | None:
    """A pilgrimage's brand counts, recounted on the stored route within its request's area."""
    if stored.request.brand is None:
        return None
    masks = request_masks(loaded.spatial, loaded.data, stored.request)
    return brand_counts(loaded.data, stored.request.brand, stored.nodes, masks.active)


def _alphabet_letters(api: "Api", data: GraphData, stored: StoredRoute) -> dict[str, Any] | None:
    """An alphabet route's letters, recounted on the stored route up to its free last leg, with
    the build's letters worked out once (`Api.letters`)."""
    if stored.request.objective != "alphabet":
        return None
    version = str(data.meta["graph_version"])
    letters = api.letters.get(version)
    if letters is None:
        letters = api.letters[version] = edge_letters(data)
    free_from = int(stored.metrics_json["alphabet"]["free_from"])
    return alphabet_counts(data, stored.edges, free_from, letters)


@router.get("/brands")
def brands(region: str, request: Request) -> BrandsOut:
    """The region's brands, the most located first, then by name."""
    data = region_of(api_of(request), region).data
    locations = np.diff(data.brand_indptr).tolist()
    out = [
        BrandOut(id=k, name=name, wikidata=wikidata, locations=located)
        for k, (name, wikidata, located) in enumerate(
            zip(data.strings.brands, data.strings.brand_wikidata, locations, strict=True)
        )
    ]
    out.sort(key=lambda b: (-b.locations, b.name, b.id))
    return BrandsOut(region=region, brands=out)


@router.get("/routes/{route_id}/geojson")
def route_geojson(route_id: str, request: Request) -> dict[str, Any]:
    """The route, its baseline and its stops as a GeoJSON FeatureCollection."""
    api = api_of(request)
    stored = stored_route(api, route_id)
    m = stored.metrics_json
    loaded = build_of(api, stored)
    return feature_collection(
        loaded.data,
        geometry_of(loaded),
        edges=stored.edges,
        length_m=m["length_m"],
        time_s=m["time_s"],
        baseline_edges=stored.baseline_edges,
        baseline_length_m=m["baseline_length_m"],
        baseline_time_s=m["baseline_time_s"],
        snaps=stored.stops,
    )


@router.post("/routes/{route_id}/validate")
def validate(route_id: str, request: Request) -> ValidationOut:
    """The validator's report on a stored route, its stops re-derived from its request."""
    api = api_of(request)
    stored = stored_route(api, route_id)
    _check_engine(stored)
    loaded = build_of(api, stored)
    masks = request_masks(loaded.spatial, loaded.data, stored.request)
    try:
        stops = resolve_stops(loaded.data, loaded.spatial, loaded.backend, stored.request, masks)
    except (SnapError, StopUnreachable) as error:
        message = f"its request cannot be planned on this build: {error}"
        raise ApiError(409, "BUILD_UNAVAILABLE", message) from None
    report = validate_route(
        loaded.data,
        stored,
        stop_nodes=[s.node for s in stops],
        node_mask=masks.active,
        banned_edges=masks.banned,
        banned_turn_classes=stored.request.constraints.banned_turn_classes,
        hard=stored.request.constraints.hard,
        alphabet=stored.request.objective == "alphabet",
    )
    return ValidationOut(
        route_id=stored.route_id,
        graph_version=stored.graph_version,
        ok=report.ok,
        checks=[
            CheckOut(name=c.name, ok=c.ok, index=c.index, detail=c.detail) for c in report.checks
        ],
    )
