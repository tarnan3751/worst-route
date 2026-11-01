"""Route files (`docs/reference/engine.md`, **Route files**).

`route_document(data, result)` is the `route.json` of a planned route:

    {graph_version, region, build_id, seed, engine_version,
     request:  the RouteRequest as JSON (with `reorder`, its stops in the order visited),
     stops:    [{lat, lon, node, moved_m, note}]  (where each stop was snapped),
     order:    each stop's position among those asked for (the identity without `reorder`),
     nodes, edges, stop_idx,
     metrics:  {length_m, time_s, delay_s (at signals and stop signs), baseline_length_m,
                baseline_time_s, baseline_delay_s, ratio (null for a round trip, whose
                baseline is 0 m), turns: {left, right, straight, u_turn}, items: {toll_booth,
                level_crossing, roundabout, bridge} (`count_items`, whatever the mode), the
                objective's own numbers (a collecting route's item, a pilgrimage's brand: {id,
                name, visits, of}, an alphabet route's alphabet: {letters, sequence, roads,
                alpha_from, free_from}), and for a ratio or duration target, target: {kind,
                value, achieved, within_tolerance}},
     run:      {attempts, accepted, stopped_because, elapsed_s, backend, and for a certified
                route certified: {status, bound_m, gap, nodes, edges, leash_m, solve_s,
                workers} (`exact.Certificate`)}}

`document(data, request, snaps, route, baseline, run=..., target=..., order=...)` is the same
document from its parts, with the run's numbers given as they are (`order` the identity when not
given): `route_document` builds it from a plan's result, a route drawn by hand
(`engine/draw.py`) from its own.

`read_route(path)` reads one back as a `SavedRoute`, which the validator reads like a `Route`
(its metrics' counters hold the file's `items`, a pilgrimage's `brand` and an alphabet route's
`alphabet`, which the validator recounts; a file from before the worst order has no `order`,
read as the identity):
`worst-route validate` re-checks it against the build it names. A run that stopped on time is
replayed exactly with `max_iterations` set to its `run.attempts` and a budget in seconds at
least as large as the first run's.

A route's GeoJSON is `presentation/geojson.py`'s.
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
from worst_route.engine.route import IntArray, Metrics, Route
from worst_route.engine.seed_route import Baseline
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
    order: tuple[int, ...]
    nodes: IntArray
    edges: IntArray
    stop_idx: tuple[int, ...]
    metrics: Metrics
    document: dict[str, Any]


def route_document(data: GraphData, result: PlanResult) -> dict[str, Any]:
    """The `route.json` of a planned route."""
    route, run = result.route, result.run
    return document(
        data,
        result.request,
        result.snaps,
        route,
        result.baseline,
        run={
            "attempts": run.attempts,
            "accepted": run.accepted,
            "stopped_because": run.stopped_because,
            "elapsed_s": run.elapsed_s,
            "backend": result.backend,
            **({"certified": result.certified.as_dict()} if result.certified else {}),
        },
        target=result.goal.describe(result.goal.value(route.score, route.metrics)),
        order=result.order,
    )


def document(
    data: GraphData,
    request: RouteRequest,
    snaps: Sequence[Snap],
    route: Route,
    baseline: Baseline,
    *,
    run: dict[str, Any],
    target: dict[str, Any] | None = None,
    order: Sequence[int] | None = None,
) -> dict[str, Any]:
    """The `route.json` of `route`, planned or drawn for `request` on these stops against this
    baseline: `run` its `{attempts, accepted, stopped_because, elapsed_s, backend}`, `target` a
    ratio or duration target's `{kind, value, achieved, within_tolerance}`, `order` each stop's
    position among those asked for (the identity when not given)."""
    ratio = route.metrics.length_m / baseline.length_m if baseline.length_m > 0 else None
    return {
        "graph_version": data.meta["graph_version"],
        "region": data.meta["region"],
        "build_id": data.meta["build_id"],
        "seed": request.seed,
        "engine_version": ENGINE_VERSION,
        "request": request.model_dump(mode="json"),
        "stops": [
            {"lat": s.lat, "lon": s.lon, "node": s.node, "moved_m": s.moved_m, "note": s.note}
            for s in snaps
        ],
        "order": list(order) if order is not None else list(range(len(snaps))),
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
        "run": run,
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
        order=tuple(int(k) for k in doc.get("order", range(len(doc["stops"])))),
        nodes=np.array(doc["nodes"], dtype=np.int32),
        edges=np.array(doc["edges"], dtype=np.int32),
        stop_idx=tuple(int(k) for k in doc["stop_idx"]),
        metrics=Metrics(
            float(doc["metrics"]["length_m"]),
            float(doc["metrics"]["time_s"]),
            {k: doc["metrics"][k] for k in ("items", "brand", "alphabet") if k in doc["metrics"]},
        ),
        document=doc,
    )
