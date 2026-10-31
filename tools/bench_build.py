"""Run a region's pipeline steps one after another and measure each: time, memory, output sizes.

    uv run python tools/bench_build.py --region oregon [--source PATH] [--steps filter,build,...]
        [--force] [--allow-fragmented] [--max-rss-gb GB] [--min-free-gb GB]
        [--out tools/out/bench_build-<region>.json]

It runs on real extracts under `WORST_ROUTE_DATA`, so it is not part of the gate, and it never
downloads: its steps are `filter` to `qa` (`docs/reference/pipeline.md`), by default all seven in
order. Each step is `worst-route pipeline <step> --region R` in a process of its own, with
`--source` passed to `filter` (the extract to read instead of `raw/<state>-latest.osm.pbf`),
`--force` to every step and `--allow-fragmented` to `cleanup` and `tiers` when given. A step
that is up to date is skipped by the CLI as usual, and its row says so; `--force` measures every
step afresh.

Per step it records the wall time, the user and system CPU seconds and the peak resident set
size of the step's process (`os.wait4`'s `ru_maxrss`, what `/usr/bin/time -l` reports on macOS),
the exit code, the step's output, and the size of the files it wrote: the filtered roads and
POIs, the stage pickles and reports, the build's `arrays/`, `geometry/` (the edges' shapes, in
the compact format), `strings.json` and `meta.json`, the `qa/` layers; and the free space on the
data directory's volume before the step.

Two guards for a heavy run: with `--max-rss-gb`, a watchdog samples the resident set size of the
step's processes every `WATCH_INTERVAL_S` and, past the limit, stops the step (SIGTERM, on which
a pipeline step unwinds and removes its temporary files, then SIGKILL after `KILL_GRACE_S`), the
row saying at what size and after how long; with `--min-free-gb`, a
step is not started while the data directory's volume has less free, and the run stops there
(exit code 3). The first step that fails, is stopped, or is not started ends the run; its row and
output are kept. It prints a table, writes the JSON report to `--out` (by default under
`tools/out/`, which git ignores) and exits with the failed step's code, else 0.
"""

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from worst_route.config import REGIONS, settings
from worst_route.pipeline import PIPELINE_VERSION, layout

STEPS = ("filter", "build", "cleanup", "enrich", "tiers", "save", "qa")
FRAGMENTED_STEPS = ("cleanup", "tiers")  # the steps that take --allow-fragmented
STAGE_FILES = {
    "build": ("stages/build",),  # the arrays engine's stages, the default's
    "cleanup": ("stages/cleanup", "stages/cleanup.json"),
    "enrich": ("stages/enrich", "stages/enrich.json"),
    "tiers": ("stages/tiers.json",),
    "save": ("arrays", "geometry", "strings.json", "meta.json"),
    "qa": ("qa",),
}
# ru_maxrss is in bytes on macOS and in kibibytes on Linux.
RSS_UNIT = 1 if sys.platform == "darwin" else 1024
WATCH_INTERVAL_S = 0.5  # how often the watchdog samples a step's resident set size
KILL_GRACE_S = 5.0  # between the watchdog's SIGTERM and its SIGKILL
DISK_STOP_CODE = 3  # the run's exit code when a step is not started for want of disk


def command() -> list[str]:
    """How to run the `worst-route` CLI of this interpreter's environment."""
    script = shutil.which("worst-route", path=str(Path(sys.executable).parent))
    if script is not None:
        return [script]
    return [sys.executable, "-c", "from worst_route.cli import app; app()"]


def step_argv(step: str, args: argparse.Namespace) -> list[str]:
    """The CLI's arguments for one step."""
    argv = ["pipeline", step, "--region", args.region]
    if step == "filter" and args.source is not None:
        argv += ["--source", str(args.source)]
    if args.force:
        argv.append("--force")
    if args.allow_fragmented and step in FRAGMENTED_STEPS:
        argv.append("--allow-fragmented")
    return argv


def size(path: Path) -> int:
    """A file's size, or the sum of the files under a directory, in bytes; 0 when absent."""
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size if path.exists() else 0


def outputs(step: str, region: str) -> dict[str, int]:
    """The files `step` writes for the region's latest build, with their sizes."""
    if step == "filter":
        files = [layout.roads_pbf(region), layout.pois_parquet(region)]
        return {str(p): size(p) for p in files}
    latest = layout.latest_file(region)
    if not latest.exists():
        return {}
    directory = layout.build_dir(region, latest.read_text().strip())
    return {str(directory / name): size(directory / name) for name in STAGE_FILES[step]}


def free_space_gb(directory: Path) -> float:
    """The free space on the volume of `directory` (or of its nearest existing parent), in GB."""
    path = directory.resolve()
    while not path.exists():
        path = path.parent
    return round(shutil.disk_usage(path).free / 1e9, 1)


def tree_rss_bytes(pid: int) -> int:
    """The resident set size of a process and its descendants, in bytes (`ps`, in KiB)."""
    listing = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True
    ).stdout
    rows = [tuple(int(v) for v in line.split()) for line in listing.splitlines() if line.strip()]
    children: dict[int, list[int]] = {}
    for child, parent, _ in rows:
        children.setdefault(parent, []).append(child)
    rss = {proc: kib for proc, _, kib in rows}
    total, todo = 0, [pid]
    while todo:
        proc = todo.pop()
        total += rss.get(proc, 0) * 1024
        todo.extend(children.get(proc, []))
    return total


def watch(pid: int, limit_gb: float, clock: float, done: threading.Event) -> dict[str, float]:
    """Stop process `pid` when its tree's resident set passes `limit_gb`; what it found, filled
    in by a daemon thread (empty while under the limit).

    The thread never reaps the process: `run_step` does, with `os.wait4`, and sets `done` once it
    has, which ends the watch. Past the limit the process gets SIGTERM, on which a pipeline step
    unwinds and removes its temporary files (`cli.exit_on_sigterm`; SIGINT would not do, since a
    shell's background job starts with it ignored), then SIGKILL if it is still running after
    `KILL_GRACE_S`. The size counts the process's descendants too, but only the process itself is
    signalled, which serves while no step starts processes of its own."""
    found: dict[str, float] = {}

    def run() -> None:
        while not done.is_set():
            size = tree_rss_bytes(pid)
            if size > limit_gb * 1e9 and not done.is_set():
                found["killed_rss_mb"] = round(size / 1e6, 1)
                found["killed_after_s"] = round(time.perf_counter() - clock, 2)
                os.kill(pid, signal.SIGTERM)
                if not done.wait(KILL_GRACE_S):
                    os.kill(pid, signal.SIGKILL)
                return
            done.wait(WATCH_INTERVAL_S)

    threading.Thread(target=run, daemon=True).start()
    return found


def run_step(step: str, args: argparse.Namespace) -> dict[str, Any]:
    """Run one step in a process of its own and measure it."""
    argv = step_argv(step, args)
    clock = time.perf_counter()
    process = subprocess.Popen(
        command() + argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    done = threading.Event()
    killed = {} if args.max_rss_gb is None else watch(process.pid, args.max_rss_gb, clock, done)
    assert process.stdout is not None
    output = []
    for line in process.stdout:  # echoed as it comes: a state-scale step runs for minutes
        print(f"  {step}: {line}", end="", flush=True)
        output.append(line.rstrip("\n"))
    process.stdout.close()
    _, status, usage = os.wait4(process.pid, 0)
    done.set()
    wall = time.perf_counter() - clock
    process.returncode = os.waitstatus_to_exitcode(status)
    return {
        **killed,
        "step": step,
        "argv": argv,
        "exit_code": process.returncode,
        "skipped": bool(output) and output[0].startswith("skipped:"),
        "wall_s": round(wall, 2),
        "user_s": round(usage.ru_utime, 2),
        "sys_s": round(usage.ru_stime, 2),
        "max_rss_mb": round(usage.ru_maxrss * RSS_UNIT / 1e6, 1),
        "outputs": outputs(step, args.region) if process.returncode == 0 else {},
        "output": output,
    }


def table(runs: list[dict[str, Any]]) -> list[str]:
    """The report as Markdown table rows."""
    rows = [
        "| Step | Wall | CPU (user + sys) | Peak RSS | Output | Result |",
        "|---|---|---|---|---|---|",
    ]
    for run in runs:
        written = sum(run["outputs"].values()) / 1e6
        if "killed_rss_mb" in run:
            result = f"killed at {run['killed_rss_mb'] / 1000:.2f} GB"
        elif run.get("not_started"):
            result = f"not started: {run['free_gb']:.1f} GB free"
        elif run["exit_code"] != 0:
            result = f"exit {run['exit_code']}"
        else:
            result = "skipped" if run["skipped"] else "ok"
        rows.append(
            f"| {run['step']} | {run['wall_s']:.1f} s | {run['user_s'] + run['sys_s']:.1f} s | "
            f"{run['max_rss_mb'] / 1000:.2f} GB | {written:.1f} MB | {result} |"
        )
    total_wall = sum(run["wall_s"] for run in runs)
    peak = max((run["max_rss_mb"] for run in runs), default=0.0)
    rows.append(f"| all | {total_wall:.1f} s | | {peak / 1000:.2f} GB | | |")
    return rows


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    steps = args.steps.split(",")
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        print(f"unknown step {unknown[0]!r}; known: {', '.join(STEPS)}", file=sys.stderr)
        return 2
    out = args.out or Path("tools/out") / f"bench_build-{args.region}.json"
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    runs: list[dict[str, Any]] = []
    for step in steps:
        free_gb = free_space_gb(settings().data_dir)
        if args.min_free_gb is not None and free_gb < args.min_free_gb:
            line = f"{step}: not started, {free_gb} GB free, below --min-free-gb {args.min_free_gb}"
            print(line, flush=True)
            runs.append(not_started(step, args, free_gb, line))
            break
        run = {**run_step(step, args), "free_gb": free_gb}
        runs.append(run)
        rss, code = run["max_rss_mb"], run["exit_code"]
        print(f"{step}: {run['wall_s']:.1f} s, {rss:.0f} MB, exit {code}", flush=True)
        if run["exit_code"] != 0:
            break
    latest = layout.latest_file(args.region)
    report = {
        "region": args.region,
        "build_id": latest.read_text().strip() if latest.exists() else None,
        "pipeline_version": PIPELINE_VERSION,
        "started": started,
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpus": os.cpu_count(),
            "load_average": list(os.getloadavg()),
        },
        "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "steps": runs,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")
    print()
    print("\n".join(table(runs)))
    print(f"wrote {out}")
    return next((run["exit_code"] for run in runs if run["exit_code"] != 0), 0)


def not_started(step: str, args: argparse.Namespace, free_gb: float, line: str) -> dict[str, Any]:
    """The row of a step not started for want of disk."""
    return {
        "step": step,
        "argv": step_argv(step, args),
        "exit_code": DISK_STOP_CODE,
        "not_started": True,
        "skipped": False,
        "wall_s": 0.0,
        "user_s": 0.0,
        "sys_s": 0.0,
        "max_rss_mb": 0.0,
        "outputs": {},
        "output": [line],
        "free_gb": free_gb,
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--region", required=True, choices=sorted(REGIONS))
    parser.add_argument("--source", type=Path, default=None, help="the extract filter reads")
    parser.add_argument("--steps", default=",".join(STEPS), help="comma-separated, in order")
    parser.add_argument("--force", action="store_true", help="pass --force to every step")
    parser.add_argument("--allow-fragmented", action="store_true")
    parser.add_argument("--max-rss-gb", type=float, default=None, help="stop a step past it")
    parser.add_argument("--min-free-gb", type=float, default=None, help="start no step below it")
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
