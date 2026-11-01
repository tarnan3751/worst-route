"""A route drawn by hand (`docs/reference/api.md`, **Drawing a route**).

    POST /draw          {request, path} -> the drawing: its stops, where it is, the stops it has
                        reached, its length and ratio, its line and its legal next moves
    POST /draw/finish   {request, path} -> {route_id, ratio}: the finished drawing, stored

Both take the request drawn on as `POST /jobs` takes it (its region among its fields) and the path
drawn, its edges in order from the start, and answer from the path alone: nothing of a drawing is
kept between calls. A drawing is the longest route with no target (`draw.drawing_problem`: another
objective or target is `INVALID_REQUEST` on its field), drawn from its stops, one route (a
`base_route_id`, a portfolio or `certified` is refused too). The request is refused as
`POST /jobs` refuses it (its objective's misfits, each stop outside the region's bbox, then the
pre-flight's snapping and re-snap fallback, `routes_api.preflight`), and the path then by
`draw.check_drawing`: an illegal one is 422 `ILLEGAL_PATH` with `refusal`, the check's code, and
`index`, its first illegal edge (`path[:index]` is legal).

What every step on one request needs (its rules: the resolved stops, the active nodes and banned
edges, the constraints; the snapped stops; the baseline; the radius bound) is kept for the last
`DRAW_CONTEXTS` requests drawn on (`DrawContexts`, by build and by the request's stops, tier, leash
and constraints, under a lock), worked out again on a miss: it is a function of the build and the
request, so the answers do not depend on it. On the national T3 build it costs 40–150 ms a request
and holds about 6.6 MB.

`POST /draw/finish` needs the path to have reached the end (else 422 `DRAWING_UNFINISHED`, with the
stops `reached`), makes it a route (`draw.finish_drawing`: `Route.new`, scored as the distance
objective, proven by the validator on the real stops; a route the validator refuses is a bug, 500
`VALIDATION_FAILED`, logged and never stored) and stores it (`RouteStore.put`, its id the content
hash) with the request as asked, its seed filled in as `POST /jobs` fills it, as its `input`, and
the baseline's edges. Its run says it was drawn by a human (`backend: "human"`, `stopped_because:
"drawn"`). On a public server a client takes at most `PUBLIC_DRAW_PER_MINUTE` steps of the two
together in any minute, and at most `PUBLIC_FINISH_PER_MINUTE` finishes, since each stores a route
(429 `RATE_LIMITED`, + `retry_after_s`), counted once the body parses: a finish takes a step and a
finish, the steps' share asked first.
"""

import logging
import math
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import numpy as np
from fastapi import APIRouter, Request
from pydantic import ValidationError

from worst_route.api.limits import client_of
from worst_route.api.routes_api import (
    ApiError,
    api_of,
    check_inside,
    geometry_of,
    preflight,
    region_of,
)
from worst_route.api.schemas import (
    DrawAt,
    DrawFinished,
    DrawIn,
    DrawMove,
    DrawOut,
    LatLon,
    LineString,
    RouteRequestIn,
    StopSnapped,
    TurnName,
)
from worst_route.engine.draw import (
    Drawing,
    DrawingUnfinished,
    DrawRefused,
    DrawRules,
    check_drawing,
    drawing_problem,
    finish_drawing,
    next_moves,
)
from worst_route.engine.request import RouteRequest, objective_problem
from worst_route.engine.seed_route import Baseline, baseline_route
from worst_route.engine.turns import TURN_NAMES, turn_classes
from worst_route.engine.validator import ValidationFailed
from worst_route.graph.data import GraphData
from worst_route.graph.loader import Loaded
from worst_route.graph.spatial import Snap
from worst_route.presentation.geojson import line_coordinates

if TYPE_CHECKING:
    from worst_route.api.app import Api

log = logging.getLogger(__name__)
router = APIRouter()


@dataclass(frozen=True, eq=False)
class DrawContext:
    """What every step of a drawing on one request needs: its rules, its snapped stops, its
    baseline and the radius bound its objective is made with."""

    rules: DrawRules
    snaps: tuple[Snap, ...]
    baseline: Baseline
    r_max: float


class DrawContexts:
    """The contexts of the last `size` requests drawn on, the one used last kept longest."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._lock = threading.Lock()
        self._kept: OrderedDict[Hashable, DrawContext] = OrderedDict()

    def get(self, key: Hashable, make: Callable[[], DrawContext]) -> DrawContext:
        """The context kept for `key`, else `make()`'s, kept (two first calls may both make
        it)."""
        with self._lock:
            kept = self._kept.get(key)
            if kept is not None:
                self._kept.move_to_end(key)
                return kept
        made = make()
        with self._lock:
            self._kept[key] = made
            self._kept.move_to_end(key)
            while len(self._kept) > self.size:
                self._kept.popitem(last=False)
        return made


@router.post("/draw")
def draw(body: DrawIn, request: Request) -> DrawOut:
    """The drawing `body.path` is on `body.request`, with its legal next moves."""
    api = api_of(request)
    _count(api, request)
    loaded, _, context = _drawn_on(api, body.request, seed=body.request.seed or 0)
    data = loaded.data
    drawing = _checked(data, context.rules, body.path)
    geometry = geometry_of(loaded)
    stop_nodes = context.rules.stop_nodes
    wanted = len(drawing.reached)  # the stop due next, the only one a move can reach
    moves = next_moves(data, context.rules, drawing)
    turns: list[TurnName | None] = [None] * len(moves)
    if len(drawing.edges) and len(moves):
        last = np.full(len(moves), drawing.edges[-1], dtype=np.int32)
        turns = [cast(TurnName, TURN_NAMES[int(c)]) for c in turn_classes(data, last, moves)]
    length_m = math.fsum(data.edge_length_m[drawing.edges].astype(np.float64).tolist())
    baseline_m = context.baseline.length_m
    return DrawOut(
        stops=[
            StopSnapped(
                input=LatLon(lat=asked.lat, lon=asked.lon),
                label=asked.label,
                node=s.node,
                lat=s.lat,
                lon=s.lon,
                moved_m=s.moved_m,
                note=s.note,
            )
            for asked, s in zip(body.request.stops or [], context.snaps, strict=True)
        ],
        at=DrawAt(node=drawing.at, **_lat_lon(data, drawing.at)),
        reached=list(drawing.reached),
        length_m=length_m,
        baseline_length_m=baseline_m,
        ratio=length_m / baseline_m if drawing.done and baseline_m > 0 else None,
        geometry=(
            LineString(coordinates=line_coordinates(data, geometry, drawing.edges))
            if len(drawing.edges)
            else None
        ),
        next=[
            DrawMove(
                edge=int(e),
                node=int(data.edge_v[e]),
                **_lat_lon(data, int(data.edge_v[e])),
                name=_name(data, int(e)),
                length_m=float(data.edge_length_m[e]),
                turn=turns[k],
                stop=wanted if data.edge_v[e] == stop_nodes[wanted] else None,
                coords=line_coordinates(data, geometry, moves[k : k + 1]),
            )
            for k, e in enumerate(moves)
        ],
        done=drawing.done,
    )


@router.post("/draw/finish")
def finish(body: DrawIn, request: Request) -> DrawFinished:
    """The finished drawing `body.path`, validated and stored as a route."""
    api = api_of(request)
    _count(api, request)
    _count_finish(api, request)
    seed = body.request.seed if body.request.seed is not None else secrets.randbelow(2**31)
    loaded, engine_request, context = _drawn_on(api, body.request, seed=seed)
    try:
        document = finish_drawing(
            loaded.data,
            engine_request,
            context.rules,
            context.snaps,
            context.baseline,
            body.path,
            r_max=context.r_max,
        )
    except DrawRefused as error:
        raise _illegal(error) from None
    except DrawingUnfinished as error:
        raise ApiError(422, "DRAWING_UNFINISHED", str(error), reached=error.reached) from None
    except ValidationFailed as error:
        log.error("a drawing the checks passed failed validation: %s", error)
        raise ApiError(500, "VALIDATION_FAILED", str(error)) from None
    asked = body.request.model_copy(update={"seed": seed}).model_dump(mode="json")
    route_id = api.store.put(document, input=asked, baseline_edges=context.baseline.edges)
    return DrawFinished(route_id=route_id, ratio=document["metrics"]["ratio"])


def _count(api: "Api", request: Request) -> None:
    """Count a step of the client's on a public server; RATE_LIMITED beyond its share."""
    if api.draw_quota is None:
        return
    wait = api.draw_quota.take(client_of(request.scope, api.settings.trust_proxy))
    if wait is not None:
        steps = api.draw_quota.per_minute
        message = f"at most {steps} drawing steps a minute; try again in {wait} s"
        raise ApiError(429, "RATE_LIMITED", message, retry_after_s=wait)


def _count_finish(api: "Api", request: Request) -> None:
    """Count a finish of the client's on a public server; RATE_LIMITED beyond its share."""
    if api.finish_quota is None:
        return
    wait = api.finish_quota.take(client_of(request.scope, api.settings.trust_proxy))
    if wait is not None:
        finishes = api.finish_quota.per_minute
        message = f"at most {finishes} finished drawings a minute; try again in {wait} s"
        raise ApiError(429, "RATE_LIMITED", message, retry_after_s=wait)


def _drawn_on(
    api: "Api", asked: RouteRequestIn, *, seed: int
) -> tuple[Loaded, RouteRequest, DrawContext]:
    """The build, the engine's request (with `seed`) and the context a drawing on `asked` needs,
    refusing what a drawing may not ask and what `POST /jobs` refuses."""
    loaded = region_of(api, asked.region)
    problem = drawing_problem(asked.objective, asked.target.kind, asked.reorder)
    if problem is not None:
        raise ApiError(422, "INVALID_REQUEST", problem[1], field=problem[0])
    if asked.base_route_id is not None:
        message = "a drawing starts from its stops, not from a route"
        raise ApiError(422, "INVALID_REQUEST", message, field="base_route_id")
    if asked.portfolio > 1:
        message = "a drawing is drawn by hand: no portfolio"
        raise ApiError(422, "INVALID_REQUEST", message, field="portfolio")
    if asked.certified:
        message = "a drawing is drawn by hand: no certificate"
        raise ApiError(422, "INVALID_REQUEST", message, field="certified")
    stops = asked.stops or []
    count = len(stops)
    problem = objective_problem(asked.objective, asked.item, asked.target.kind, asked.brand, count)
    if problem is not None:
        raise ApiError(422, "INVALID_REQUEST", problem[1], field=problem[0])
    for k, stop in enumerate(stops):
        check_inside(loaded, stop.lat, stop.lon, stop_index=k)
    try:
        engine_request = asked.engine_request(seed)
    except ValidationError as error:
        raise ApiError(422, "INVALID_REQUEST", str(error)) from None
    key = (
        asked.region,
        loaded.data.meta["graph_version"],
        tuple(engine_request.stops),
        engine_request.tier,
        engine_request.leash_km,
        engine_request.constraints,
    )
    context = api.draw_contexts.get(key, lambda: _context(loaded, engine_request))
    return loaded, engine_request, context


def _context(loaded: Loaded, request: RouteRequest) -> DrawContext:
    """The context of a drawing on `request`: the pre-flight's masks and stops, the baseline."""
    prepared = preflight(loaded, request)
    masks, nodes = prepared.masks, [s.node for s in prepared.snaps]
    baseline = baseline_route(loaded.backend, loaded.data, nodes, masks.active, masks.banned)
    return DrawContext(
        DrawRules.of(masks, nodes, request.constraints),
        prepared.snaps,
        baseline,
        min(masks.leash_m, masks.extent_m),
    )


def _checked(data: GraphData, rules: DrawRules, path: list[int]) -> Drawing:
    """The drawing `path` is, else ILLEGAL_PATH."""
    try:
        return check_drawing(data, rules, path)
    except DrawRefused as error:
        raise _illegal(error) from None


def _illegal(error: DrawRefused) -> ApiError:
    return ApiError(422, "ILLEGAL_PATH", str(error), refusal=error.code, index=error.index)


def _lat_lon(data: GraphData, v: int) -> dict[str, float]:
    """A node's position, 7 decimals, as OSM writes positions."""
    return {"lat": round(float(data.node_lat[v]), 7), "lon": round(float(data.node_lon[v]), 7)}


def _name(data: GraphData, e: int) -> str | None:
    """An edge's name, else its ref, else None."""
    name, ref = int(data.edge_name[e]), int(data.edge_ref[e])
    if name >= 0:
        return data.strings.names[name]
    return data.strings.refs[ref] if ref >= 0 else None
