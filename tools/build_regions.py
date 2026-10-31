"""Build regions one after another, each measured by `bench_build.py`: a city list, or the states.

    uv run python tools/build_regions.py (--regions portland ... | --states) [--max-mem-gb 18]
        [--min-free-gb 15] [--out tools/out/build_regions.json]

The regions are those named (`config.REGIONS`: a city's bbox region like `portland`, a state's,
the national ones), or with `--states` every region of one state named by its slug, the 48
contiguous states and DC (`docs/reference/pipeline.md`, **The state builds**). They run smallest
extract first, so that the biggest come last. For each, `tools/bench_build.py` runs `filter` to
`qa` under its memory watchdog and disk floor, its report under `tools/out/regions/`; a `cleanup`
or `tiers` that finds the graph too fragmented is run again with `--allow-fragmented` (the steps
before it skipped as up to date), and its QA then says what was dropped. Every build whose QA ran
is offered to `pipeline prune`, which takes one that passed, or one cleaned with
`--allow-fragmented` that failed only the connectivity rule, and refuses the rest, whose stages
stay; every build written gets its places index (`aux places`). A step stopped by the watchdog or
not started for want of disk ends the batch, with that step's code; any other failure, QA's
included, is recorded and the batch goes on. A region whose latest build, at this pipeline version,
already holds a QA report is not run again: its row is read from that report, and it is offered to
`prune` again, so rerunning the batch prunes what an earlier run left. The batch prints a table of
the regions and rewrites its JSON report to `--out` after each (by default under `tools/out/`,
which git ignores).

Like `bench_build.py` it reads real extracts under `WORST_ROUTE_DATA`, never downloads and is not
part of the gate; with `--states` it is a heavy job, the better part of an hour on a laptop, one
region at a time.
"""

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from worst_route.config import REGIONS, STATES
from worst_route.pipeline import PIPELINE_VERSION, layout
from worst_route.pipeline.qa import CONNECTIVITY_THRESHOLDS, SCC_LARGEST_DROPPED, SCC_RETAINED

TOOLS = Path(__file__).resolve().parent
CLI = [sys.executable, "-c", "from worst_route.cli import app; app()"]
FRAGMENTED_STEPS = ("cleanup", "tiers")  # the steps that refuse a graph too fragmented


def state_regions() -> list[str]:
    """The regions of one state named by its slug: the contiguous states and DC."""
    return [name for name, region in REGIONS.items() if name in STATES and region.states == (name,)]


def extract_bytes(region: str) -> float:
    """The size of the region's extracts; infinite when one is missing, so that it runs last."""
    paths = [layout.raw_pbf(state) for state in REGIONS[region].states]
    if not all(path.exists() for path in paths):
        return math.inf
    return float(sum(path.stat().st_size for path in paths))


def bench(region: str, args: argparse.Namespace, fragmented: bool) -> tuple[int, dict[str, Any]]:
    """`bench_build.py` on the region, its output echoed as it comes: its code and report."""
    suffix = "-fragmented" if fragmented else ""
    out = args.out.parent / "regions" / f"bench_build-{region}{suffix}.json"
    out.unlink(missing_ok=True)
    argv = [sys.executable, str(TOOLS / "bench_build.py"), "--region", region, "--out", str(out)]
    if args.max_mem_gb is not None:
        argv += ["--max-mem-gb", str(args.max_mem_gb)]
    if args.min_free_gb is not None:
        argv += ["--min-free-gb", str(args.min_free_gb)]
    if fragmented:
        argv.append("--allow-fragmented")
    code = subprocess.run(argv).returncode
    return code, {**json.loads(out.read_text()), "report": str(out)}


def halted(report: dict[str, Any]) -> dict[str, Any] | None:
    """The step that ends the batch: one the watchdog stopped or that was not started for want
    of disk."""
    steps = report["steps"]
    if steps and ("killed_mem_mb" in steps[-1] or steps[-1].get("not_started")):
        return dict(steps[-1])
    return None


def too_fragmented(report: dict[str, Any]) -> bool:
    """Whether the run ended at a `cleanup` or `tiers` that refused a graph too fragmented."""
    last = report["steps"][-1] if report["steps"] else None
    if last is None or last["exit_code"] == 0 or last["step"] not in FRAGMENTED_STEPS:
        return False
    return any(line.startswith(f"{last['step']} failed: ") for line in last["output"])


def cli(*argv: str) -> tuple[int, list[str], list[str]]:
    """A `worst-route` command, its output echoed: its code, stdout lines and stderr lines."""
    result = subprocess.run([*CLI, *argv], capture_output=True, text=True)
    out, err = result.stdout.splitlines(), result.stderr.splitlines()
    for line in out + err:
        print(f"  {argv[1]}: {line}", flush=True)
    return result.returncode, out, err


def prune(region: str) -> dict[str, Any]:
    """`pipeline prune` on the region's latest build: whether it pruned (or had nothing left to),
    and its last line."""
    code, out, err = cli("pipeline", "prune", "--region", region)
    return {"pruned": code == 0, "prune": (out if code == 0 else err or out)[-1]}


def written(region: str) -> tuple[Path, dict[str, Any] | None] | None:
    """The region's latest build at this pipeline version, if written, and its QA report."""
    latest = layout.latest_file(region)
    if not latest.exists():
        return None
    directory = layout.build_dir(region, latest.read_text().strip())
    meta = directory / "meta.json"
    if not meta.exists() or json.loads(meta.read_text())["pipeline_version"] != PIPELINE_VERSION:
        return None
    qa = layout.qa_file(directory, "qa.json")
    return directory, json.loads(qa.read_text()) if qa.exists() else None


def row(region: str, reports: list[dict[str, Any]]) -> dict[str, Any]:
    """The region's row: its build and QA as written, its runs' time and peak memory."""
    steps = [step for report in reports for step in report["steps"]]
    last = reports[-1]["steps"] if reports else []
    failed = next((s["step"] for s in last if s["exit_code"] != 0 and s["step"] != "qa"), None)
    found = written(region)
    qa = found[1] if found else None
    result: dict[str, Any] = {
        "region": region,
        "build_id": found[0].name if found else None,
        "fragmented": any(report["args"]["allow_fragmented"] for report in reports),
        "wall_s": round(sum(step["wall_s"] for step in steps), 1),
        "max_mem_mb": max((step.get("max_mem_mb", 0.0) for step in steps), default=0.0),
        "free_gb": steps[0]["free_gb"] if steps else None,
        "failed_step": failed,
        "reports": [report["report"] for report in reports],
    }
    if qa is not None:
        result |= {
            "graph_version": qa["graph_version"],
            "nodes": qa["counts"]["nodes"],
            "edges": qa["counts"]["edges"],
            "km": qa["counts"]["km"],
            "megabytes": qa["save"]["megabytes"],
            "geometry_megabytes": qa["save"]["geometry_megabytes"],
            "qa": "passed" if qa["passed"] else "failed",
            "qa_failed": {t["name"]: t["value"] for t in qa["thresholds"] if not t["passed"]},
            "largest_dropped": qa["cleanup"]["connectivity"]["largest_dropped"][:3],
        }
    return result


def table(rows: list[dict[str, Any]]) -> list[str]:
    """The regions as Markdown table rows."""
    lines = [
        "| Region | Build | Nodes | Edges | Road km | Size | Time | Peak memory | QA |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        pruned = r.get("pruned", False)
        if "qa" in r:
            failed = ", ".join(f"{name} {value:g}" for name, value in r["qa_failed"].items())
            qa = "passed" if not failed else f"failed: {failed}"
            if failed and set(r["qa_failed"]) <= CONNECTIVITY_THRESHOLDS and pruned:
                retained = r["qa_failed"].get(SCC_RETAINED)
                largest = r["qa_failed"].get(SCC_LARGEST_DROPPED)
                parts = [f"retained {retained:.3f}"] if retained is not None else []
                parts += [f"largest {largest:,.0f} nodes"] if largest is not None else []
                qa = f"fragmented: {', '.join(parts)}, pruned"
            size = f"{r['megabytes'] + r['geometry_megabytes']:.1f} MB"
            counts = f"{r['nodes']:,} | {r['edges']:,} | {r['km']:,.0f}"
        else:
            qa, size, counts = f"{r['failed_step'] or 'no'} step failed", "", " | | "
        if r["fragmented"] and not qa.startswith("fragmented"):
            qa += " (fragmented)"
        lines.append(
            f"| {r['region']} | {r['build_id'] or ''} | {counts} | {size} | {r['wall_s']:.0f} s | "
            f"{r['max_mem_mb'] / 1000:.2f} GB | {qa} |"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    regions = state_regions() if args.states else list(args.regions)
    unknown = [region for region in regions if region not in REGIONS]
    if unknown:
        print(f"unknown region {unknown[0]!r}", file=sys.stderr)
        return 2
    regions.sort(key=lambda region: (extract_bytes(region), region))
    report: dict[str, Any] = {
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipeline_version": PIPELINE_VERSION,
        "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "regions": [],
        "halted": None,
    }
    code = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for region in regions:
        found = written(region)
        if found is not None and found[1] is not None:  # QA reached on an earlier run
            print(f"{region}: done before, build {found[0].name}", flush=True)
            reports = args.out.parent / "regions"
            names = (f"bench_build-{region}.json", f"bench_build-{region}-fragmented.json")
            earlier = [
                {**json.loads(path.read_text()), "report": str(path)}
                for path in (reports / name for name in names)
                if path.exists()
            ]
            result = {**row(region, earlier), "done_before": True}
            result |= prune(region)
            report["regions"].append(result)
            args.out.write_text(json.dumps(report, indent=1) + "\n")
            continue
        print(f"{region}: started at {time.strftime('%H:%M:%S')}", flush=True)
        status, run = bench(region, args, fragmented=False)
        runs = [run]
        if halted(run) is None and too_fragmented(run):
            status, run = bench(region, args, fragmented=True)
            runs.append(run)
        stop = halted(run)
        result = row(region, runs)
        if stop is None and "qa" in result:
            result |= prune(region)
        if stop is None and result["build_id"] is not None:
            places, out, err = cli("aux", "places", "--region", region)
            result["places"] = out[-1] if places == 0 else f"failed: {(err or out)[-1]}"
            result["places_warnings"] = err if places == 0 else []
        report["regions"].append(result)
        if stop is not None:
            report["halted"] = {"region": region, **stop}
        args.out.write_text(json.dumps(report, indent=1) + "\n")
        if stop is not None:
            code = status
            break
    print()
    print("\n".join(table(report["regions"])))
    print(f"wrote {args.out}")
    return code


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--regions", nargs="+", metavar="REGION", help="regions to build")
    which.add_argument("--states", action="store_true", help="every contiguous state and DC")
    parser.add_argument("--max-mem-gb", type=float, default=18.0, help="bench_build.py's")
    parser.add_argument("--min-free-gb", type=float, default=15.0, help="bench_build.py's")
    parser.add_argument("--out", type=Path, default=Path("tools/out/build_regions.json"))
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
