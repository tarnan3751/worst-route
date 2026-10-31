"""A route as GeoJSON (`presentation/geojson.py`)."""

import json

import numpy as np
import pytest

from worst_route.engine.plan import PlanResult, plan_route
from worst_route.engine.request import RouteRequest
from worst_route.presentation.geojson import feature_collection, line_coordinates, route_geojson

from ..engine.graphs import at_node, lattice

GRID = lattice(8, 8, rounded_lonlat=True)


def planned(stops: list[int], seed: int = 2) -> PlanResult:
    request = RouteRequest(
        stops=[at_node(GRID, v) for v in stops], seed=seed, max_iterations=150, budget_s=120
    )
    return plan_route(GRID, request)


def test_a_round_trip_has_no_baseline_line() -> None:
    features = route_geojson(GRID, planned([27, 27]))["features"]
    assert features[1]["properties"]["kind"] == "baseline" and features[1]["geometry"] is None


def test_geojson_draws_the_route_the_baseline_and_the_stops() -> None:
    result = planned([0, 27, 63])
    collection = route_geojson(GRID, result)
    assert collection["type"] == "FeatureCollection"
    route, baseline, *stops = collection["features"]
    assert route["properties"]["kind"] == "route" and baseline["properties"]["kind"] == "baseline"
    line = np.array(route["geometry"]["coordinates"])
    assert len(line) == len(result.route.nodes)  # one point per node: shared junctions kept once
    start = result.route.nodes[0]
    assert line[0] == pytest.approx([GRID.node_lon[start], GRID.node_lat[start]], abs=1e-5)
    assert route["properties"]["length_m"] == result.route.metrics.length_m
    assert len(baseline["geometry"]["coordinates"]) == len(result.baseline.nodes)
    assert [s["properties"]["node"] for s in stops] == [0, 27, 63]
    assert stops[1]["geometry"]["coordinates"] == [
        round(result.snaps[1].lon, 7),
        round(result.snaps[1].lat, 7),
    ]
    json.dumps(collection)  # JSON-native throughout


def test_the_collection_from_a_route_s_parts_is_route_geojson_s() -> None:
    result = planned([0, 27, 63])
    route, baseline = result.route, result.baseline
    parts = feature_collection(
        GRID,
        edges=route.edges,
        length_m=route.metrics.length_m,
        time_s=route.metrics.time_s,
        baseline_edges=baseline.edges,
        baseline_length_m=baseline.length_m,
        baseline_time_s=baseline.time_s,
        snaps=result.snaps,
    )
    assert json.dumps(parts) == json.dumps(route_geojson(GRID, result))
    line = parts["features"][0]["geometry"]["coordinates"]
    assert line_coordinates(GRID, route.edges) == line
