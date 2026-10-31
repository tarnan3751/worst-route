"""Drive a built desktop app headless (`docs/reference/desktop.md`, **The smoke test**): what the
release workflow runs on each platform's frozen app, and a person on theirs.

    uv run python tools/desktop_smoke.py APP [--home DIR] [--budget 10] [--long]
        [--out tools/out/desktop_smoke.json]

APP is the frozen app's executable (`.../Worst Route.app/Contents/MacOS/Worst Route`, or
`.../worst-route-desktop[.exe]`). It starts `APP --headless --port <free> --geocoder none` with
its user directory in `--home` (by default a fresh temporary one, so the start is a first start:
Numba compiles), and then:

- waits for `/api/health` and times the start;
- checks `/api/ready` (every check passing) and `/api/regions` against the regions of the app's
  manifest, in order (its `desktop.json`: in the `.app`'s `Contents/Resources/data/`, else in
  `_internal/data/` beside the executable);
- plans Powell's to Reed College in Portland and Portland to Bend (and, with `--long`, New York
  to Los Angeles), each with seed 1 and `--budget` seconds, on the region `POST /regions/pick`
  names: the job's events read until `done`, the stored route's `validation.ok`, and
  `POST /routes/{id}/validate` passing it again; on macOS and Linux it samples the resident memory
  of the app's process and of its workers while each job runs;
- stops the app, which must leave none of its processes behind: by SIGTERM on macOS and Linux,
  on which it must exit 0 within a minute; on Windows, where the frozen app is windowed (no console
  for a Ctrl+Break to reach), by terminating it, its workers ended with their jobs.

It prints a JSON report (and writes it to `--out`) and exits non-zero at the first failure. It
talks to the app on this machine's loopback only; the app's geocoder is off and it fetches no
basemap headless.
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

TRIPS = {
    "Powell's -> Reed College": [(45.5231, -122.6765), (45.4817, -122.6263)],
    "Portland -> Bend": [(45.5231, -122.6765), (44.0582, -121.3153)],
}
LONG = {"New York -> Los Angeles": [(40.7128, -74.006), (34.0522, -118.2437)]}
WINDOWS = sys.platform == "win32"


def manifest_of(app: Path) -> dict[str, Any]:
    """The frozen app's `desktop.json`: the `.app`'s on macOS, else beside the executable."""
    for path in (
        app.parent.parent / "Resources" / "data" / "desktop.json",
        app.parent / "_internal" / "data" / "desktop.json",
    ):
        if path.is_file():
            return dict(json.loads(path.read_text()))
    raise SystemExit(f"no data/desktop.json in the app of {app}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def processes_of(app: Path) -> list[tuple[int, int]]:
    """(pid, resident kB) of every process running `app` (its workers are `app` again)."""
    if WINDOWS:
        listed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {app.name}"],
            capture_output=True,
            text=True,
        ).stdout
        rows = [line.split('","') for line in listed.splitlines() if line.startswith('"')]
        return [(int(r[1]), 0) for r in rows if len(r) > 1]
    listed = subprocess.run(["ps", "-Ao", "pid=,rss=,command="], capture_output=True, text=True)
    found = []
    for line in listed.stdout.splitlines():
        pid, rss, command = line.split(None, 2)
        if command.startswith(str(app)):
            found.append((int(pid), int(rss)))
    return found


def plan(
    http: httpx.Client, app: Path, main_pid: int, stops: list[tuple[float, float]], budget: float
) -> dict[str, Any]:
    """One trip: picked, planned, validated twice; its numbers and the memory seen."""
    body = [{"lat": lat, "lon": lon} for lat, lon in stops]
    started = time.perf_counter()
    region = http.post("/regions/pick", json={"stops": body}).json()["region"]
    job = {"region": region, "stops": body, "seed": 1, "budget_s": budget}
    created = http.post("/jobs", json=job)
    if created.status_code != 202:
        raise SystemExit(f"POST /jobs refused: {created.text}")
    trip: dict[str, Any] = {"region": region}
    seen: dict[str, float] = {}
    memory = {"app_mb": 0.0, "worker_mb": 0.0}
    end: dict[str, Any] = {}
    with http.stream("GET", created.json()["events_url"], timeout=budget + 120) as stream:
        kind = ""
        for line in stream.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
                seen.setdefault(kind, round(time.perf_counter() - started, 2))
                if kind == "progress" and not WINDOWS:
                    for pid, rss in processes_of(app):
                        key = "app_mb" if pid == main_pid else "worker_mb"
                        memory[key] = max(memory[key], rss / 1024)
            elif line.startswith("data: ") and kind in ("done", "error"):
                end = {kind: json.loads(line[6:])}
                break
    if "done" not in end:
        raise SystemExit(f"the job ended without a route: {end}")
    route_id = end["done"]["route_id"]
    document = http.get(f"/routes/{route_id}").json()
    again = http.post(f"/routes/{route_id}/validate").json()
    if not document["validation"]["ok"] or not again.get("ok"):
        raise SystemExit(f"route {route_id} failed the validator: {again}")
    metrics = document["metrics"]
    trip.update(
        stopped_because=end["done"]["stopped_because"],
        ratio=round(metrics["ratio"], 2),
        length_km=round(metrics["length_m"] / 1000),
        validated=True,
        events_s=seen,
    )
    if not WINDOWS:
        trip["resident"] = {k: round(v) for k, v in memory.items()}
    return trip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("app", type=Path)
    parser.add_argument("--home", type=Path, help="the app's user directory (default: fresh)")
    parser.add_argument("--budget", type=float, default=10.0)
    parser.add_argument("--long", action="store_true", help="New York to Los Angeles too")
    parser.add_argument("--out", type=Path, default=Path("tools/out/desktop_smoke.json"))
    args = parser.parse_args()
    app = args.app.resolve()
    home = args.home or Path(tempfile.mkdtemp(prefix="worst-route-desktop-"))
    port = free_port()
    manifest = manifest_of(app)
    report: dict[str, Any] = {"app": str(app), "home": str(home)}
    report["first_start"] = not (home / "numba").exists()  # no kernels compiled yet
    started = time.perf_counter()
    process = subprocess.Popen(
        [str(app), "--headless", "--port", str(port), "--geocoder", "none"],
        env={**os.environ, "WORST_ROUTE_DESKTOP_HOME": str(home)},
    )
    http = httpx.Client(base_url=f"http://127.0.0.1:{port}/api", timeout=60)
    try:
        while True:
            try:
                if http.get("/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if process.poll() is not None or time.perf_counter() - started > 600:
                raise SystemExit(f"the app did not start (exit {process.poll()})")
            time.sleep(0.2)
        report["start_s"] = round(time.perf_counter() - started, 2)
        ready = http.get("/ready")
        report["ready"] = {c["name"]: c["detail"] for c in ready.json()["checks"]}
        if ready.status_code != 200:
            raise SystemExit(f"/ready is not ready: {ready.text}")
        report["regions"] = [r["region"] for r in http.get("/regions").json()]
        if report["regions"] != manifest["regions"]:
            raise SystemExit(f"/regions serves {report['regions']}, not {manifest['regions']}")
        trips = {**TRIPS, **(LONG if args.long else {})}
        for name, stops in trips.items():
            report[name] = plan(http, app, process.pid, stops, args.budget)
            print(name, json.dumps(report[name]), flush=True)
    finally:
        if WINDOWS:
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        try:
            report["exit"] = process.wait(60)
        except subprocess.TimeoutExpired:
            process.kill()
            report["exit"] = "killed after a minute"
        time.sleep(5)
        report["left_behind"] = [pid for pid, _ in processes_of(app)]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1) + "\n")
        print(json.dumps(report, indent=1))
    if (report["exit"] != 0 and not WINDOWS) or report["left_behind"]:
        raise SystemExit("the app did not stop cleanly")


if __name__ == "__main__":
    main()
