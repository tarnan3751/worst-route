"""Route files (`docs/reference/engine.md`, **Route files**).

`route_document(data, result)` is the `route.json` of a planned route:

    {graph_version, region, build_id, seed, engine_version,
     request:  the RouteRequest as JSON,
     stops:    [{lat, lon, node, moved_m, note}]  (where each stop was snapped),
     nodes, edges, stop_idx,
     metrics:  {length_m, time_s, delay_s (at signals and stop signs), baseline_length_m,
                baseline_time_s, baseline_delay_s, ratio (null for a round trip, whose
                baseline is 0 m), turns: {left, right, straight, u_turn}, items: {toll_booth,
                level_crossing, roundabout, bridge} (`count_items`, whatever the mode), the
                objective's own numbers (a collecting route's item, a pilgrimage's brand: {id,
                name, visits, of}), and for a ratio or duration target, target: {kind, value,
                achieved, within_tolerance}},
     run:      {attempts, accepted, stopped_because, elapsed_s, backend}}

`read_route(path)` reads one back as a `SavedRoute`, which the validator reads like a `Route`
(its metrics' counters hold the file's `items` and a pilgrimage's `brand`, which the validator
recounts):
`worst-route validate` re-checks it against the build it names. A run that stopped on time is
replayed exactly with `max_iterations` set to its `run.attempts` and a budget in seconds at
least as large as the first run's.

`route_geojson(data, result)` is a FeatureCollection: the route and the baseline as
LineStrings along their edges' geometry (`[lon, lat]`, 7 decimals, a point repeated where two
edges meet kept once; the baseline of a round trip has no geometry), and one Point per stop.
`feature_collection(...)` makes the same from the route's parts, for a route that was stored
rather than planned in this process (the API's), and `line_coordinates(data, edges)` is one such
line's points.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from worst_route.engine import ENGINE_VERSION
from worst_route.engine.objectives.collect import count_items
from worst_route.engine.plan import PlanResult
from worst_route.engine.request import RouteRequest
from worst_route.engine.route import IntArray, Metrics
from worst_route.engine.turns import count_turns
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import Snap


@dataclass(frozen=True, eq=False)
class SavedRoute:
    """A route read from a `route.json`."""

    graph_version: str
    region: str
    build_id: str
    seed: int
    engine_version: str
    request: RouteRequest
    stops: tuple[Snap, ...]
    nodes: IntArray
    edges: IntArray
    stop_idx: tuple[int, ...]
    metrics: Metrics
    document: dict[str, Any]


def route_document(data: GraphData, result: PlanResult) -> dict[str, Any]:
    """The `route.json` of a planned route."""
    route, baseline, run = result.route, result.baseline, result.run
    ratio = route.metrics.length_m / baseline.length_m if baseline.length_m > 0 else None
    target = result.goal.describe(result.goal.value(route.score, route.metrics))
    return {
        "graph_version": data.meta["graph_version"],
        "region": data.meta["region"],
        "build_id": data.meta["build_id"],
        "seed": result.request.seed,
        "engine_version": ENGINE_VERSION,
        "request": result.request.model_dump(mode="json"),
        "stops": [
            {"lat": s.lat, "lon": s.lon, "node": s.node, "moved_m": s.moved_m, "note": s.note}
            for s in result.snaps
        ],
        "nodes": route.nodes.tolist(),
        "edges": route.edges.tolist(),
        "stop_idx": list(route.stop_idx),
        "metrics": {
            "length_m": route.metrics.length_m,
            "time_s": route.metrics.time_s,
            "delay_s": route.metrics.delay_s,
            "baseline_length_m": baseline.length_m,
            "baseline_time_s": baseline.time_s,
            "baseline_delay_s": baseline.delay_s,
            "ratio": ratio,
            "turns": count_turns(data, route.edges),
            "items": count_items(data, route.nodes, route.edges),
            **route.metrics.counters,
            **({"target": target} if target is not None else {}),
        },
        "run": {
            "attempts": run.attempts,
            "accepted": run.accepted,
            "stopped_because": run.stopped_because,
            "elapsed_s": run.elapsed_s,
            "backend": result.backend,
        },
    }


def read_route(path: Path) -> SavedRoute:
    """A `route.json` as a `SavedRoute`; `KeyError`, `TypeError`, `ValueError` or `OverflowError`
    (an id beyond int32) when it is not one."""
    doc = json.loads(path.read_text())
    return SavedRoute(
        graph_version=str(doc["graph_version"]),
        region=str(doc["region"]),
        build_id=str(doc["build_id"]),
        seed=int(doc["seed"]),
        engine_version=str(doc["engine_version"]),
        request=RouteRequest.model_validate(doc["request"]),
        stops=tuple(
            Snap(
                node=int(s["node"]),
                moved_m=float(s["moved_m"]),
                lat=float(s["lat"]),
                lon=float(s["lon"]),
                note=s["note"],
            )
            for s in doc["stops"]
        ),
        nodes=np.array(doc["nodes"], dtype=np.int32),
        edges=np.array(doc["edges"], dtype=np.int32),
        stop_idx=tuple(int(k) for k in doc["stop_idx"]),
        metrics=Metrics(
            float(doc["metrics"]["length_m"]),
            float(doc["metrics"]["time_s"]),
            {k: doc["metrics"][k] for k in ("items", "brand") if k in doc["metrics"]},
        ),
        document=doc,
    )


def route_geojson(data: GraphData, result: PlanResult) -> dict[str, Any]:
    """The route, the baseline and the stops as a GeoJSON FeatureCollection."""
    route, baseline = result.route, result.baseline
    return feature_collection(
        data,
        edges=route.edges,
        length_m=route.metrics.length_m,
        time_s=route.metrics.time_s,
        baseline_edges=baseline.edges,
        baseline_length_m=baseline.length_m,
        baseline_time_s=baseline.time_s,
        snaps=result.snaps,
    )


def feature_collection(
    data: GraphData,
    *,
    edges: IntArray,
    length_m: float,
    time_s: float,
    baseline_edges: IntArray,
    baseline_length_m: float,
    baseline_time_s: float,
    snaps: Sequence[Snap],
) -> dict[str, Any]:
    """`route_geojson`'s FeatureCollection, from the route's parts."""
    lines = [
        _line(data, edges, "route", length_m, time_s),
        _line(data, baseline_edges, "baseline", baseline_length_m, baseline_time_s),
    ]
    stops = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(s.lon, 7), round(s.lat, 7)]},
            "properties": {
                "kind": "stop",
                "index": k,
                "node": s.node,
                "moved_m": s.moved_m,
                "note": s.note,
            },
        }
        for k, s in enumerate(snaps)
    ]
    return {"type": "FeatureCollection", "features": [*lines, *stops]}


def line_coordinates(data: GraphData, edges: IntArray) -> list[list[float]]:
    """The `[lon, lat]` points along `edges`, 7 decimals, a point repeated where two edges meet
    kept once."""
    starts = data.coord_indptr[edges]
    counts = data.coord_indptr[edges + 1] - starts
    within = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    points = data.coords[np.repeat(starts, counts) + within].astype(np.float64)
    fresh = np.ones(len(points), dtype=np.bool_)
    fresh[1:] = (points[1:] != points[:-1]).any(axis=1)
    coordinates: list[list[float]] = np.round(points[fresh], 7).tolist()
    return coordinates


def _line(
    data: GraphData, edges: IntArray, kind: str, length_m: float, time_s: float
) -> dict[str, Any]:
    properties = {"kind": kind, "length_m": length_m, "time_s": time_s}
    if len(edges) == 0:
        return {"type": "Feature", "geometry": None, "properties": properties}
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": line_coordinates(data, edges)},
        "properties": properties,
    }
