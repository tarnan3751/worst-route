"""The M1 acceptance run on a saved build (`docs/PLAN.md` §7 M1 step 9).

    uv run python tools/acceptance.py --region portland [--build-id ID] [--pairs 20] [--seed 1]
        [--budget 30] [--backend auto|csr|python] [--min-km 3] [--max-km 15] [--jaccard 5]
        [--pair-seed 2026] [--near-m 250] [--tier auto|T0..T4] [--leash-km auto|X] [--places]
        [--out tools/out/acceptance.json]

It needs a build under `WORST_ROUTE_DATA`, so it is not part of the gate. M1's acceptance is
`--region portland --pair-seed 2026 --pairs 20 --seed 1 --budget 30` (the other options at their
defaults). `--backend` is `make_backend`'s: by default `auto`, the Numba kernels when numba
imports (M1's numbers were measured on the reference); the report records which one ran. A run
bounded by time goes further on a faster backend, so the routes can differ by backend where a
run stops on time rather than converging.

**Drawing the pairs.** One `Generator(PCG64(--pair-seed))` draws every point, in order. A point
is a latitude, then a longitude, each uniform within the region's bbox (the nodes' extent for a
region without one); it is rejected, and the next one drawn, unless a node of the build lies
within `--near-m` metres of it, which drops points in rivers and forests. A pair draws its start,
then up to 200 ends: the first end whose straight-line distance from the start lies within
[`--min-km`, `--max-km`] completes the pair; when none does, the start is dropped and the pair
drawn again. The distance is Euclidean between the two points projected into the build's CRS
(`SpatialIndex.project`), the measure a request's tier is chosen by, taken before the points are
rounded to 5 decimals (about 1 m) for the request and the report. With `--places` a point is a
place of the region's places index instead (`worst-route aux places`; towns and CDPs alike),
drawn uniformly by its row and put at its polygon's `point_on_surface`, with the same rejection
by `--near-m`.

Each pair is planned with `plan_route` and `--seed`, `--budget` seconds and no attempt limit, so
the budget is what bounds a run, at `--tier` and `--leash-km` (both `auto` by default; a leash of
at most 150 km, as a request's). The first `--jaccard` pairs that plan are planned again with
`--seed` + 1 and the two routes compared (Jaccard of their edge ids and of their road units). A
pair that cannot be planned is resolved again on the build without its transition rows, so the
report says whether the transition tables are what stopped it; a stop moved by the re-snap
fallback is checked the same way. Each run reports how many candidates the splice refused for a
turn a table forbids (`refused_transitions`), which the turn-aware searches should keep at zero.

It prints one table row per pair and a summary, and writes the JSON report to `--out` (by
default under `tools/out/`, which git ignores). It exits 1 when a planned route fails the
validator (an engine bug), else 0, whatever the ratios.
"""

import argparse
import dataclasses
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, get_args

import numpy as np
from scipy.spatial import cKDTree

from worst_route.config import DEFAULT_BACKEND, LEASH_MAX_KM, REGIONS, BackendName
from worst_route.engine.plan import PlanResult, plan_route
from worst_route.engine.request import RouteRequest, TierChoice, request_masks
from worst_route.engine.seed_route import resolve_stops
from worst_route.engine.snap import StopUnreachable
from worst_route.engine.validator import ValidationFailed
from worst_route.graph.backend import GraphBackend
from worst_route.graph.backends import make_backend
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SnapError, SpatialIndex
from worst_route.graph.store import MissingArray, read_build
from worst_route.pipeline import layout
from worst_route.presentation.places import PlaceIndex

UNLIMITED = 10**9  # attempts: the budget in seconds bounds a run
RESNAP_NOTE = "nearer nodes cannot be reached"

type Point = tuple[float, float]  # (lat, lon)


def place_points(region: str) -> np.ndarray | None:
    """(lon, lat) of each place of the region's places index, inside its polygon; None without
    an index."""
    index = PlaceIndex.load(region)
    return None if index is None else np.array([(p.lon, p.lat) for p in index.town_points()])


def draw_pairs(
    data: GraphData,
    spatial: SpatialIndex,
    bbox: tuple[float, float, float, float],
    args: argparse.Namespace,
    places: np.ndarray | None = None,
) -> list[tuple[Point, Point, float]]:
    """(start, end, straight-line metres) for each pair, drawn as the module docstring says:
    within `bbox`, or at `places` ((lon, lat) rows) when given."""
    rng = np.random.Generator(np.random.PCG64(args.pair_seed))
    tree = cKDTree(np.column_stack([data.node_x, data.node_y]))
    lon0, lat0, lon1, lat1 = bbox

    def point() -> tuple[Point, np.ndarray]:
        while True:
            if places is None:
                lat, lon = rng.uniform(lat0, lat1), rng.uniform(lon0, lon1)
            else:
                lon, lat = places[rng.integers(len(places))]
            xy = np.array(spatial.project(lat, lon))
            if tree.query(xy)[0] <= args.near_m:
                return (round(float(lat), 5), round(float(lon), 5)), xy

    pairs: list[tuple[Point, Point, float]] = []
    while len(pairs) < args.pairs:
        start, start_xy = point()
        for _ in range(200):
            end, end_xy = point()
            distance = float(np.hypot(*(end_xy - start_xy)))
            if args.min_km * 1000 <= distance <= args.max_km * 1000:
                pairs.append((start, end, distance))
                break
    return pairs


def plan(
    data: GraphData,
    backend: GraphBackend,
    spatial: SpatialIndex,
    stops: list[Point],
    seed: int,
    args: argparse.Namespace,
) -> tuple[PlanResult | None, str | None]:
    """The planned route, or None and the error that stopped it."""
    request = RouteRequest(
        stops=stops,
        seed=seed,
        budget_s=args.budget,
        max_iterations=UNLIMITED,
        tier=args.tier,
        leash_km=args.leash_km,
    )
    try:
        return plan_route(data, request, backend=backend, spatial=spatial), None
    except (SnapError, StopUnreachable) as error:
        return None, f"{type(error).__name__}: {error}"
    except ValidationFailed as error:
        failed = [line for line in error.report.lines() if "FAILED" in line]
        return None, "ValidationFailed: " + "; ".join(failed)


def transitions_matter(
    data: GraphData,
    backend: GraphBackend,
    spatial: SpatialIndex,
    stops: list[Point],
    seed: int,
    args: argparse.Namespace,
) -> bool:
    """Whether the stops resolve differently on the build without its transition rows."""
    empty = np.zeros(0, dtype=np.int32)
    free = dataclasses.replace(data, trans_node=empty, trans_in=empty, trans_out=empty)
    request = RouteRequest(stops=stops, seed=seed, tier=args.tier, leash_km=args.leash_km)
    masks = request_masks(spatial, data, request)
    outcomes: list[object] = []
    for graph in (data, free):
        try:
            snaps = resolve_stops(graph, spatial, backend, request, masks)
            outcomes.append([(s.node, s.note) for s in snaps])
        except (SnapError, StopUnreachable) as error:
            outcomes.append(str(error))
    return outcomes[0] != outcomes[1]


def jaccard(a: np.ndarray, b: np.ndarray) -> float:
    sa, sb = set(a.tolist()), set(b.tolist())
    return len(sa & sb) / len(sa | sb)


def main(args: argparse.Namespace) -> int:
    region = REGIONS[args.region]
    build_id = args.build_id or layout.latest_file(args.region).read_text().strip()
    try:
        data = read_build(layout.build_dir(args.region, build_id))
    except MissingArray as error:
        print(error, file=sys.stderr)
        return 1
    spatial = SpatialIndex(data)
    backend = make_backend(data, args.backend)
    bbox = region.bbox or (
        float(data.node_lon.min()),
        float(data.node_lat.min()),
        float(data.node_lon.max()),
        float(data.node_lat.max()),
    )
    places = None
    if args.places:
        places = place_points(args.region)
        if places is None:
            print(f"no places index: run `worst-route aux places --region {args.region}`")
            return 1
    pairs = draw_pairs(data, spatial, bbox, args, places)

    runs: list[dict[str, Any]] = []
    routes: dict[int, PlanResult] = {}
    print("| # | Stops | km | Baseline km | Worst km | Ratio | Attempts / time | Result |")
    print("|---|---|---|---|---|---|---|---|")
    for k, (start, end, distance) in enumerate(pairs):
        result, error = plan(data, backend, spatial, [start, end], args.seed, args)
        run: dict[str, Any] = {"pair": k, "start": start, "end": end, "km": distance / 1000}
        if result is None:
            run |= {"planned": False, "error": error}
            if error is not None and error.startswith("StopUnreachable"):
                run["transitions_matter"] = transitions_matter(
                    data, backend, spatial, [start, end], args.seed, args
                )
            row = f"– | – | – | – | {error}"
        else:
            routes[k] = result
            baseline = result.baseline.length_m
            worst = result.route.metrics.length_m
            notes = [s.note for s in result.snaps if s.note]
            run |= {
                "planned": True,
                "validator": result.report.ok,
                "tier": result.masks.tier,
                "baseline_km": baseline / 1000,
                "worst_km": worst / 1000,
                "ratio": worst / baseline,
                "attempts": result.run.attempts,
                "accepted": result.run.accepted,
                "elapsed_s": result.run.elapsed_s,
                "stopped": result.run.stopped_because,
                "refused_transitions": result.run.refused_transitions,
                "notes": notes,
            }
            if any(RESNAP_NOTE in note for note in notes):
                run["transitions_matter"] = transitions_matter(
                    data, backend, spatial, [start, end], args.seed, args
                )
            rate = result.run.attempts / max(result.run.elapsed_s, 1e-9)
            row = (
                f"{baseline / 1000:.2f} | {worst / 1000:.2f} | {worst / baseline:.2f}× | "
                f"{result.run.attempts} / {result.run.elapsed_s:.1f} s ({rate:.0f}/s) | "
                + ("ok" if result.report.ok else "VALIDATOR FAILED")
                + "".join(f" ({note})" for note in notes)
            )
        runs.append(run)
        stops = f"{start[0]},{start[1]} → {end[0]},{end[1]}"
        print(f"| {k:02d} | {stops} | {distance / 1000:.1f} | {row} |", flush=True)

    comparisons: list[dict[str, Any]] = []
    for k in sorted(routes)[: args.jaccard]:
        start, end, _ = pairs[k]
        other, error = plan(data, backend, spatial, [start, end], args.seed + 1, args)
        if other is None:
            comparisons.append({"pair": k, "error": error})
            continue
        first, second = routes[k].route, other.route
        comparisons.append(
            {
                "pair": k,
                "ratio": other.route.metrics.length_m / other.baseline.length_m,
                "edges": jaccard(first.edges, second.edges),
                "units": jaccard(data.edge_unit[first.edges], data.edge_unit[second.edges]),
            }
        )

    planned = [r for r in runs if r["planned"]]
    rates = [r["attempts"] / max(r["elapsed_s"], 1e-9) for r in planned]
    summary = {
        "pairs": len(runs),
        "planned": len(planned),
        "validator_passed": sum(r["validator"] for r in planned),
        "median_ratio": statistics.median(r["ratio"] for r in planned) if planned else None,
        "ratio_range": [min(r["ratio"] for r in planned), max(r["ratio"] for r in planned)]
        if planned
        else None,
        "median_attempts_per_s": statistics.median(rates) if rates else None,
        "refused_transitions": sum(r["refused_transitions"] for r in planned),
        "unplanned_by_transitions": sum(
            1 for r in runs if not r["planned"] and r.get("transitions_matter")
        ),
        "moved_by_transitions": sum(
            1 for r in runs if r["planned"] and r.get("transitions_matter")
        ),
        "jaccard_edges": [c["edges"] for c in comparisons if "edges" in c],
    }
    report = {
        "region": args.region,
        "build_id": build_id,
        "graph_version": data.meta["graph_version"],
        "backend": backend.name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "summary": summary,
        "runs": runs,
        "jaccard": comparisons,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n")
    print()
    for key, value in summary.items():
        print(f"{key}: {value}")
    for c in comparisons:
        print(f"jaccard pair {c['pair']:02d}: {c.get('edges', c.get('error'))}")
    print(f"wrote {args.out}")
    return 1 if summary["validator_passed"] < len(planned) else 0


def leash_km(text: str) -> float | str:
    """`auto`, or a leash in km within (0, LEASH_MAX_KM], as a request takes it."""
    if text == "auto":
        return text
    km = float(text)
    if not 0 < km <= LEASH_MAX_KM:
        raise argparse.ArgumentTypeError(f"a leash is auto or within (0, {LEASH_MAX_KM:g}] km")
    return km


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--region", required=True, choices=sorted(REGIONS))
    parser.add_argument("--build-id", default=None, help="default: the region's latest")
    parser.add_argument("--pairs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--budget", type=float, default=30.0, help="seconds per run")
    parser.add_argument("--backend", choices=get_args(BackendName), default=DEFAULT_BACKEND)
    parser.add_argument("--min-km", type=float, default=3.0)
    parser.add_argument("--max-km", type=float, default=15.0)
    parser.add_argument("--jaccard", type=int, default=5, help="pairs planned with seed + 1")
    parser.add_argument("--pair-seed", type=int, default=2026)
    parser.add_argument("--near-m", type=float, default=250.0, help="a node within this")
    parser.add_argument("--tier", choices=get_args(TierChoice), default="auto")
    parser.add_argument("--leash-km", type=leash_km, default="auto", help="auto or km")
    parser.add_argument("--places", action="store_true", help="draw points at Census places")
    parser.add_argument("--out", type=Path, default=Path("tools/out/acceptance.json"))
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main(parse_args()))
