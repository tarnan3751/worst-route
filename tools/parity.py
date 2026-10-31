"""Both pipeline engines on one region's filtered roads: the same build, and what each step cost.

    uv run python tools/parity.py --region portland [--engines networkx,arrays]
        [--allow-fragmented] [--work DIR] [--out tools/out/parity-REGION.json]

It reads the region's filtered roads and POIs under `WORST_ROUTE_DATA` (`pipeline filter` first)
and writes nothing there: each engine runs `build`, `cleanup` and then enrich, tiers and save's
build (its arrays and geometry, by `pipeline.save`'s own functions), each on its own engine's
stages as the CLI runs them, into its own directory under `--work` (a temporary directory,
removed at the end, unless given). Each step runs in a process of its own, so its seconds and
its peak resident memory are its own; the step after it reads the stage it wrote, as the
pipeline does. A state is a heavy job: run one only when nothing else heavy runs on the machine.

Then it compares the engines: the cleanup, enrich and tiers reports (seconds aside) and the
builds, array by array, the geometry's too (dtype and shape first, then bytes and the rows that
differ), with their strings and graph_version. It prints a table of the steps and a line per
engine compared, writes the JSON report to `--out` (by default under `tools/out/`, which git
ignores), and exits 1 when the engines differ, else 0.
"""

import argparse
import json
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, get_args

STEPS = ("build", "cleanup", "save")


def run_step(engine: str, step: str, region: str, work: Path, allow_fragmented: bool) -> None:
    """One step of one engine, in this process; prints its seconds and peak memory as JSON."""
    import pickle

    import pyarrow.parquet as pq

    from worst_route.config import REGIONS
    from worst_route.pipeline import build, cleanup, enrich, layout, save, tiers
    from worst_route.pipeline.arrays import build as arrays_build
    from worst_route.pipeline.arrays import cleaned
    from worst_route.pipeline.arrays import enrich as arrays_enrich
    from worst_route.pipeline.arrays import tiers as arrays_tiers

    spec = REGIONS[region]
    directory = work / engine
    clock = time.perf_counter()
    if step == "build":
        if engine == "arrays":
            raw, stats = arrays_build.build_raw_arrays(
                layout.roads_pbf(region), spec.crs_epsg, spec.road_classes
            )
            arrays_build.save_stage(raw, stats, directory)
        else:
            graph, stats = build.build_raw_graph(
                layout.roads_pbf(region), spec.crs_epsg, spec.road_classes
            )
            build.save_stage(graph, stats, directory)
    elif step == "cleanup":
        if engine == "arrays":
            raw = arrays_build.load_stage(directory)
            arrays, report = cleanup.run_arrays_cleanup(raw, allow_fragmented=allow_fragmented)
            cleaned.save_stage(arrays, report, directory)
        else:
            with layout.stage_file(directory, "build.pkl").open("rb") as f:
                source = pickle.load(f)
            graph, report = cleanup.run_cleanup(source, allow_fragmented=allow_fragmented)
            cleanup.save_stage(graph, report, directory)
    else:  # enrich, tiers and the build, as `pipeline enrich`, `tiers` and `save` run them
        pois = pq.read_table(layout.pois_parquet(region))
        meta = save.build_meta(spec, engine, {"region": region}, [])
        if engine == "arrays":
            enriched, enrich_report = arrays_enrich.run_enrich(cleaned.load_stage(directory), pois)
            arrays_enrich.save_stage(enriched, enrich_report, directory)
            # the build from the stages read back, as `pipeline save` reads them
            enriched = arrays_enrich.load_stage(cleaned.load_stage(directory), directory)
            report = arrays_tiers.run_tiers(enriched.graph, allow_fragmented=allow_fragmented)
            data = save.graph_data_of(enriched, [t.node_ids for t in report.tiers], meta)
            geometry = save.graph_geometry_of(enriched, data)
        else:
            with layout.stage_file(directory, "cleanup.pkl").open("rb") as f:
                graph = pickle.load(f)
            enrich.save_stage(graph, enrich.run_enrich(graph, pois), directory)
            report = tiers.run_tiers(graph, allow_fragmented=allow_fragmented)
            data = save.graph_data(graph, [t.node_ids for t in report.tiers], meta)
            geometry = save.graph_geometry(graph, data)
        tiers.save_stage(report, directory)
        save.save_build(data, directory, geometry)
    seconds = time.perf_counter() - clock
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / 1e6 if sys.platform == "darwin" else peak / 1e3  # bytes on macOS, else KiB
    print(json.dumps({"seconds": round(seconds, 2), "peak_mb": round(peak_mb, 1)}))


def compare(work: Path, engines: list[str]) -> dict[str, Any]:
    """The differences between the first engine's outputs and each other's."""
    import numpy as np

    from worst_route.graph.data import ARRAY_SPECS
    from worst_route.graph.geometry import GEOMETRY_SPECS, read_geometry
    from worst_route.graph.store import read_build
    from worst_route.pipeline import layout

    def report(engine: str) -> dict[str, Any]:
        """The engine's cleanup, enrich and tiers reports, seconds aside."""
        reports = {}
        for name in ("cleanup", "enrich", "tiers"):
            loaded = json.loads(layout.stage_file(work / engine, f"{name}.json").read_text())
            reports[name] = {key: value for key, value in loaded.items() if key != "seconds"}
        return reports

    base = engines[0]
    want = read_build(work / base, mmap=False)
    want_shapes = read_geometry(work / base, want.m, mmap=False)
    result: dict[str, Any] = {"graph_version": want.meta["graph_version"], "engines": {}}
    for engine in engines[1:]:
        got = read_build(work / engine, mmap=False)
        got_shapes = read_geometry(work / engine, got.m, mmap=False)
        pairs = [(name, getattr(want, name), getattr(got, name)) for name in ARRAY_SPECS]
        pairs += [
            (f"geometry/{name}", getattr(want_shapes, name), getattr(got_shapes, name))
            for name in GEOMETRY_SPECS
        ]
        arrays: list[dict[str, Any]] = []
        for name, a, b in pairs:
            if a.dtype != b.dtype or a.shape != b.shape:
                shapes = f"{a.dtype.str}{list(a.shape)} against {b.dtype.str}{list(b.shape)}"
                arrays.append({"array": name, "shape differs": shapes})
            elif a.tobytes() != b.tobytes():
                rows = int(np.count_nonzero(np.any((a != b).reshape(len(a), -1), axis=1)))
                arrays.append({"array": name, "rows": rows})
        result["engines"][engine] = {
            "graph_version": got.meta["graph_version"],
            "same_report": report(engine) == report(base),
            "same_strings": got.strings == want.strings,
            "arrays_differing": arrays,
        }
    return result


def summary(result: dict[str, Any], engines: list[str]) -> tuple[list[str], bool]:
    """A line per engine compared with the first, and whether they are all the same."""
    lines, same = [], True
    for engine, found in result["engines"].items():
        version = found["graph_version"] == result["graph_version"]
        differing = [
            f"{a['array']} ({a['shape differs']})" if "shape differs" in a else a["array"]
            for a in found["arrays_differing"]
        ]
        same &= version and found["same_report"] and found["same_strings"] and not differing
        lines.append(
            f"{engine} against {engines[0]}: graph_version {found['graph_version'][:12]} "
            f"({'the same' if version else 'differs'}), "
            f"reports {'the same' if found['same_report'] else 'differ'}, "
            f"strings {'the same' if found['same_strings'] else 'differ'}, "
            f"arrays differing: {', '.join(differing) or 'none'}"
        )
    return lines, same


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", required=True)
    parser.add_argument("--engines", default="networkx,arrays")
    parser.add_argument("--allow-fragmented", action="store_true")
    parser.add_argument("--work", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--step", nargs=2, metavar=("ENGINE", "STEP"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.step is not None:  # a child: one step of one engine
        run_step(*args.step, args.region, args.work, args.allow_fragmented)
        return 0

    from worst_route.config import REGIONS, PipelineEngine
    from worst_route.pipeline import layout

    engines = args.engines.split(",")
    if args.region not in REGIONS or not set(engines) <= set(get_args(PipelineEngine)):
        parser.error(f"unknown region or engine: {args.region}, {args.engines}")
    if not layout.roads_pbf(args.region).exists():
        parser.error(f"no {layout.roads_pbf(args.region)}: run `pipeline filter` first")
    work = args.work or Path(tempfile.mkdtemp(prefix="parity-"))
    steps: dict[str, dict[str, Any]] = {engine: {} for engine in engines}
    try:
        for engine in engines:
            for step in STEPS:
                command = [sys.executable, __file__, "--region", args.region, "--work", str(work)]
                command += ["--step", engine, step]
                command += ["--allow-fragmented"] if args.allow_fragmented else []
                done = subprocess.run(command, capture_output=True, text=True)
                if done.returncode != 0:
                    print(done.stderr, file=sys.stderr)
                    print(f"{engine} {step} failed", file=sys.stderr)
                    return 1
                steps[engine][step] = json.loads(done.stdout.strip().splitlines()[-1])
        result = compare(work, engines)
    finally:
        if args.work is None:
            shutil.rmtree(work, ignore_errors=True)

    print(f"{'step':10s}" + "".join(f"{engine:>24s}" for engine in engines))
    for step in STEPS:
        cells = [steps[engine][step] for engine in engines]
        print(
            f"{step:10s}"
            + "".join(f"{c['seconds']:>11.1f} s {c['peak_mb']:>7.0f} MB" for c in cells)
        )
    lines, same = summary(result, engines)
    print("\n".join(lines))
    out = args.out or Path(__file__).parent / "out" / f"parity-{args.region}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"region": args.region, "steps": steps, **result, "same": same}
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out}")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
