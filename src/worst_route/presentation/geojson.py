"""A route as GeoJSON (`docs/reference/presentation.md`, **GeoJSON**).

`route_geojson(data, result)` is a FeatureCollection: the route and the baseline as
LineStrings along their edges' geometry (`[lon, lat]`, 7 decimals, a point repeated where two
edges meet kept once; the baseline of a round trip has no geometry), and one Point per stop.
`feature_collection(...)` makes the same from the route's parts, for a route that was stored
rather than planned in this process (the API's), and `line_coordinates(data, edges)` is one such
line's points. The CLI writes it (`worst-route plan --geojson`) and the API answers it
(`GET /routes/{id}/geojson`); the engine and the job workers never import this module.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np

from worst_route.engine.plan import PlanResult
from worst_route.engine.route import IntArray
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import Snap


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
