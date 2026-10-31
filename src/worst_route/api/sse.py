"""A job's events: their payloads and their stream (`docs/reference/api.md`, **Events**).

An `Event` is one server-sent event of a job: its id (from the job's counter), its name and its
JSON data. `server_sent(event)` is the same for `EventSourceResponse`.

The geometry of a path: consecutive edges share their junction point (true of every build:
an edge's `coords` start and end at its nodes), so `path_points(data, edges)` keeps it once and
returns, with the `[lon, lat]` points (7 decimals), each node's index among them. A splice
(`splice_out`) carries the detour's points and indices, so a client can replace route nodes
i..j and still know where every node is. A snapshot (`snapshot`) carries the whole route the
same way, each edge simplified on its own by Douglas-Peucker (shapely, topology preserved),
which keeps both ends of every edge, so every node stays a vertex. The tolerance is `simplify_m`
as degrees of latitude, applied to lon/lat as they are, so the kept points are the original
values exactly (a node lands where a splice puts it, to the last digit); a degree of longitude
being shorter, the tolerance across a north-south line is less, 0.7 of it at 45°.
`route_metrics` is a route's numbers, its delays included, with its ratio to the baseline (None
for a round trip, whose baseline is 0 m); `time_ratio` is its experienced time's, driving and
delays, to the baseline's (None for a round trip too); `baseline_out` is the `baseline` event's
payload.
"""

from dataclasses import dataclass

import numpy as np
import shapely
from sse_starlette import ServerSentEvent

from worst_route.api.schemas import BaselineOut, LineString, RouteMetrics, Snapshot, SpliceOut
from worst_route.engine.route import IntArray, Metrics
from worst_route.graph.data import GraphData
from worst_route.presentation.geojson import line_coordinates

METRES_PER_DEGREE = 111_320.0  # of latitude


@dataclass(frozen=True)
class Event:
    """One event of a job's stream."""

    id: int
    name: str
    data: str


def server_sent(event: Event) -> ServerSentEvent:
    """The event as sse-starlette sends it."""
    return ServerSentEvent(data=event.data, event=event.name, id=str(event.id))


def path_points(
    data: GraphData, edges: IntArray, simplify_m: float = 0.0
) -> tuple[list[list[float]], list[int]]:
    """The `[lon, lat]` points along a path of edges, and the index of each of its nodes."""
    starts = data.coord_indptr[edges]
    counts = data.coord_indptr[edges + 1] - starts
    within = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    points = data.coords[np.repeat(starts, counts) + within].astype(np.float64)
    if simplify_m > 0 and len(edges):
        owner = np.repeat(np.arange(len(edges)), counts)
        lines = shapely.linestrings(points, indices=owner)
        simple = shapely.simplify(lines, simplify_m / METRES_PER_DEGREE, preserve_topology=True)
        points, owner = shapely.get_coordinates(simple, return_index=True)
        counts = np.bincount(owner, minlength=len(edges))
    keep = np.ones(len(points), dtype=np.bool_)
    keep[(np.cumsum(counts) - counts)[1:]] = False
    node_index = np.concatenate(([0], np.cumsum(counts - 1)))
    coords: list[list[float]] = np.round(points[keep], 7).tolist()
    return coords, node_index.tolist()


def route_metrics(metrics: Metrics, baseline_length_m: float) -> RouteMetrics:
    """A route's numbers, with its ratio to the baseline."""
    ratio = metrics.length_m / baseline_length_m if baseline_length_m > 0 else None
    return RouteMetrics(
        length_m=metrics.length_m,
        time_s=metrics.time_s,
        delay_s=metrics.delay_s,
        ratio=ratio,
        **metrics.counters,
    )


def time_ratio(
    time_s: float, delay_s: float, baseline_time_s: float, baseline_delay_s: float
) -> float | None:
    """The experienced time, driving and delays, over the baseline's (None for a round trip,
    whose baseline drives nothing)."""
    if baseline_time_s <= 0:
        return None
    return (time_s + delay_s) / (baseline_time_s + baseline_delay_s)


def splice_out(data: GraphData, i: int, j: int, nodes: IntArray, edges: IntArray) -> SpliceOut:
    """One accepted splice: route nodes i..j replaced by the detour `nodes`."""
    coords, node_index = path_points(data, edges)
    return SpliceOut(i=i, j=j, node_count=len(nodes), coords=coords, node_index=node_index)


def snapshot(
    data: GraphData,
    *,
    nodes: IntArray,
    edges: IntArray,
    stop_idx: tuple[int, ...],
    iteration: int,
    elapsed_s: float,
    metrics: RouteMetrics,
    simplify_m: float,
) -> Snapshot:
    """The whole route, every node a vertex of its simplified geometry."""
    coords, node_index = path_points(data, edges, simplify_m)
    return Snapshot(
        iteration=iteration,
        elapsed_s=elapsed_s,
        node_count=len(nodes),
        stop_idx=list(stop_idx),
        geometry=LineString(coordinates=coords),
        node_index=node_index,
        metrics=metrics,
    )


def baseline_out(data: GraphData, length_m: float, time_s: float, edges: IntArray) -> BaselineOut:
    """The `baseline` event: the sensible route, without geometry for a round trip."""
    geometry = LineString(coordinates=line_coordinates(data, edges)) if len(edges) else None
    return BaselineOut(length_m=length_m, time_s=time_s, geometry=geometry)
