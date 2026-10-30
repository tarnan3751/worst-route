"""The `worst-route` command line.

`version` prints the package version. `pipeline <step>` runs one offline pipeline step for a
named region (`docs/PLAN.md` §2): download, filter, build, cleanup, enrich, tiers, save, qa.
Every step takes `--region` (a key of `config.REGIONS`), `--build-id` and `--force`; `filter`
and `build` also take `--source`, a file read instead of the step's usual input (fixtures,
tests). `download`, `filter` and `build` are real (`docs/reference/pipeline.md`): each prints
what it wrote and its counts, or that its output is up to date and was skipped; a failed
download prints one line and exits 1. Each imports its step module when it runs, so `version`
and the other commands do not load the geo stack. The other steps are a skeleton for now: each
exits with code 2 and says it is not implemented yet.

`plan` plans a route on a saved build (`docs/reference/engine.md`, **The CLI**): it prints the
snapped stops, the baseline, the worst route, the ratio, the search and the validator's report,
with a progress line on stderr on an accepted splice, at most one every 2 s, and writes
`route.json` and GeoJSON when asked; a stop that cannot be snapped or reached, or a missing
build, is one line and exit 1, and a route that fails the validator is never printed.
`validate` re-checks a saved `route.json` against the build it names, with its stops re-derived
from its request (`resolve_stops`), and exits 1 when a check fails. `serve` arrives with the
API.
"""

import json
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import typer

from worst_route import __version__
from worst_route.config import DEFAULT_BUDGET_S, DEFAULT_MAX_ITERATIONS, REGIONS
from worst_route.pipeline import PIPELINE_VERSION, layout

if TYPE_CHECKING:
    from worst_route.graph.data import GraphData

app = typer.Typer(help="The least efficient way to get anywhere in the US.", no_args_is_help=True)
pipeline_app = typer.Typer(
    help="Offline pipeline steps, one region at a time.", no_args_is_help=True
)
app.add_typer(pipeline_app, name="pipeline")


def _check_region(value: str) -> str:
    if value not in REGIONS:
        raise typer.BadParameter(f"unknown region {value!r}; known: {', '.join(sorted(REGIONS))}")
    return value


RegionOption = Annotated[
    str, typer.Option(help="Region name from config.REGIONS.", callback=_check_region)
]
BuildIdOption = Annotated[str | None, typer.Option(help="Build id to write or reuse.")]
ForceOption = Annotated[
    bool, typer.Option("--force", help="Redo the step even if its output exists.")
]
SourceOption = Annotated[
    Path | None,
    typer.Option(
        help="Read this file instead of the step's usual input (fixtures, tests).",
        exists=True,
        dir_okay=False,
    ),
]


def _not_implemented(step: str) -> NoReturn:
    typer.echo(f"not implemented yet: pipeline {step}", err=True)
    raise typer.Exit(code=2)


def _require(path: Path, hint: str) -> None:
    if not path.exists():
        typer.echo(f"missing input {path}: {hint}", err=True)
        raise typer.Exit(code=1)


def _inputs(path: Path) -> list[dict[str, str]]:
    return [{"file": path.name, "md5": layout.file_md5(path)}]


def _size(path: Path) -> str:
    return f"{path.stat().st_size / 1e6:.1f} MB"


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@pipeline_app.command()
def download(
    region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False
) -> None:
    """Fetch the region's Geofabrik state extract."""
    import httpx

    from worst_route.pipeline.download import download_state

    try:
        path, skipped = download_state(REGIONS[region].state, force=force)
    except (ValueError, httpx.HTTPError) as error:
        detail = (str(error).splitlines() or [type(error).__name__])[0]
        typer.echo(f"download failed: {detail}", err=True)
        raise typer.Exit(code=1) from None
    if skipped:
        typer.echo(f"skipped: {path} matches its md5 (--force to fetch again)")
    else:
        typer.echo(f"wrote {path} ({_size(path)}), md5 verified")


@pipeline_app.command("filter")
def filter_(
    region: RegionOption,
    build_id: BuildIdOption = None,
    force: ForceOption = False,
    source: SourceOption = None,
) -> None:
    """Keep the drivable roads and the POIs; clip to the region's bbox."""
    from worst_route.pipeline.filter import filter_roads, source_timestamp

    spec = REGIONS[region]
    src = source if source is not None else layout.raw_pbf(spec.state)
    _require(src, f"run `worst-route pipeline download --region {region}` first")
    roads, pois = layout.roads_pbf(region), layout.pois_parquet(region)
    record: dict[str, Any] = {
        "step": "filter",
        "inputs": _inputs(src),
        "source_timestamp": source_timestamp(src),
        "params": {"bbox": spec.bbox, "pipeline_version": PIPELINE_VERSION},
    }
    if not force and pois.exists() and layout.sidecar_matches(roads, record):
        typer.echo(f"skipped: {roads} and {pois} are up to date (--force to redo)")
        return
    layout.sidecar(roads).unlink(missing_ok=True)
    stats = filter_roads([src], roads, pois, spec.bbox)
    layout.write_sidecar(roads, record)
    typer.echo(f"wrote {roads} ({_size(roads)}) and {pois} ({_size(pois)})")
    typer.echo(
        f"ways: read {stats.ways_read}, kept {stats.ways_kept}, excluded {stats.ways_excluded}, "
        f"outside bbox {stats.ways_outside}, duplicate {stats.ways_duplicate}"
    )
    kinds = ", ".join(f"{kind} {n}" for kind, n in stats.pois.items())
    typer.echo(f"pois: {sum(stats.pois.values())} ({kinds or 'none'})")


@pipeline_app.command()
def build(
    region: RegionOption,
    build_id: BuildIdOption = None,
    force: ForceOption = False,
    source: SourceOption = None,
) -> None:
    """Turn the filtered extract into a raw directed graph."""
    from worst_route.pipeline.build import build_raw_graph, save_stage

    spec = REGIONS[region]
    roads = source if source is not None else layout.roads_pbf(region)
    _require(roads, f"run `worst-route pipeline filter --region {region}` first")
    inputs = _inputs(roads)
    bid = build_id or layout.build_id_for(inputs[0]["md5"], spec)
    directory = layout.build_dir(region, bid)
    output = layout.stage_file(directory, "build.pkl")
    record: dict[str, Any] = {
        "step": "build",
        "inputs": inputs,
        "params": {
            "region": region,
            "bbox": spec.bbox,
            "crs_epsg": spec.crs_epsg,
            "pipeline_version": PIPELINE_VERSION,
        },
    }
    if not force and layout.sidecar_matches(output, record):
        typer.echo(f"skipped: build {bid} is up to date at {output} (--force to redo)")
    else:
        layout.sidecar(output).unlink(missing_ok=True)
        graph, stats = build_raw_graph(roads, spec.crs_epsg)
        save_stage(graph, stats, directory)
        layout.write_sidecar(output, record)
        typer.echo(f"build {bid}: wrote {output} ({_size(output)})")
        typer.echo(
            f"ways: read {stats.ways_read}, kept {stats.ways_kept}; segments {stats.segments}, "
            f"nodes {stats.nodes}, edges {stats.edges}"
        )
        rules = ", ".join(f"{rule} {n}" for rule, n in stats.edges_by_rule.items())
        typer.echo(f"edges by one-way rule: {rules or 'none'}")
    layout.latest_file(region).write_text(f"{bid}\n")


@pipeline_app.command()
def cleanup(
    region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False
) -> None:
    """Exclusions, simplification, consolidation, carriageway twins, connectivity."""
    _not_implemented("cleanup")


@pipeline_app.command()
def enrich(
    region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False
) -> None:
    """Speeds, identities, collectibles, brands, states and places."""
    _not_implemented("enrich")


@pipeline_app.command()
def tiers(region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False) -> None:
    """Compute the tier masks."""
    _not_implemented("tiers")


@pipeline_app.command()
def save(region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False) -> None:
    """Write the build in the on-disk format."""
    _not_implemented("save")


@pipeline_app.command()
def qa(region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False) -> None:
    """Write the build's QA report and check its thresholds."""
    _not_implemented("qa")


PROGRESS_EVERY_S = 2.0
LatLonOption = Annotated[str, typer.Option(metavar="LAT,LON")]


def _latlon(text: str) -> tuple[float, float]:
    try:
        lat, lon = (float(part) for part in text.split(","))
    except ValueError:
        raise typer.BadParameter(f"{text!r}: expected LAT,LON") from None
    return lat, lon


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _load_build(region: str, build_id: str | None) -> "GraphData":
    from worst_route.graph.store import read_build

    hint = f"run `worst-route pipeline save --region {region}` first"
    if build_id is None:
        latest = layout.latest_file(region)
        if not latest.exists():
            _fail(f"no build of {region}: {hint}")
        build_id = latest.read_text().strip()
    path = layout.build_dir(region, build_id)
    if not (path / "meta.json").exists():
        _fail(f"no saved build at {path}: {hint}")
    return read_build(path)


def _duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min {secs} s"


@app.command()
def plan(
    region: RegionOption,
    from_: Annotated[str, typer.Option("--from", metavar="LAT,LON", help="The start.")],
    to: Annotated[str, typer.Option(metavar="LAT,LON", help="The end.")],
    build_id: Annotated[str | None, typer.Option(help="Build id (default: latest).")] = None,
    via: Annotated[
        list[str] | None, typer.Option(metavar="LAT,LON", help="A stop between, in order.")
    ] = None,
    seed: Annotated[int | None, typer.Option(help="Seed (default: random, printed).")] = None,
    budget: Annotated[float, typer.Option(help="Seconds to search.")] = DEFAULT_BUDGET_S,
    max_iterations: Annotated[
        int, typer.Option(help="Attempts to search at most.")
    ] = DEFAULT_MAX_ITERATIONS,
    leash_km: Annotated[str, typer.Option(help="Leash in km, or auto.")] = "auto",
    tier: Annotated[str, typer.Option(help="auto or T0..T4.")] = "auto",
    out: Annotated[Path | None, typer.Option(help="Write the route.json here.")] = None,
    geojson: Annotated[Path | None, typer.Option(help="Write the GeoJSON here.")] = None,
) -> None:
    """Plan the worst route between stops, validated."""
    from pydantic import ValidationError

    from worst_route.engine.io import route_document, route_geojson
    from worst_route.engine.plan import plan_route
    from worst_route.engine.request import RouteRequest
    from worst_route.engine.seed_route import Baseline
    from worst_route.engine.snap import StopUnreachable
    from worst_route.engine.validator import ValidationFailed
    from worst_route.engine.worsen import ProgressEvent
    from worst_route.graph.spatial import SnapError

    stops = [_latlon(from_), *(_latlon(v) for v in via or []), _latlon(to)]
    try:
        request = RouteRequest(
            stops=stops,
            tier=tier,
            leash_km=leash_km if leash_km == "auto" else float(leash_km),
            budget_s=budget,
            max_iterations=max_iterations,
            seed=seed if seed is not None else secrets.randbelow(2**31),
        )
    except ValidationError as error:
        problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]
        raise typer.BadParameter("; ".join(problems)) from None
    except ValueError as error:
        raise typer.BadParameter(f"--leash-km: {error}") from None
    data = _load_build(region, build_id)

    last = time.monotonic()

    def progress(event: ProgressEvent, baseline: Baseline) -> None:
        nonlocal last
        if time.monotonic() - last >= PROGRESS_EVERY_S:
            last = time.monotonic()
            km = event.metrics.length_m / 1000
            ratio = event.metrics.length_m / baseline.length_m if baseline.length_m else 0.0
            typer.echo(f"iteration {event.iteration}: ratio {ratio:.2f}x, {km:.2f} km", err=True)

    try:
        result = plan_route(data, request, on_progress=progress)
    except (SnapError, StopUnreachable) as error:
        _fail(f"cannot plan: {error}")
    except ValidationFailed as error:
        lines = [line for line in error.report.lines() if "FAILED" in line]
        _fail("the route failed validation (an engine bug): " + "; ".join(lines))

    route, baseline, masks, run = result.route, result.baseline, result.masks, result.run
    meta = data.meta
    typer.echo(
        f"build: {meta['region']}/{meta['build_id']} (graph_version {meta['graph_version'][:12]})"
    )
    typer.echo(
        f"request: tier {masks.tier}, leash {masks.leash_m / 1000:.1f} km, seed {request.seed}, "
        f"budget {request.budget_s:g} s or {request.max_iterations} attempts"
    )
    for k, s in enumerate(result.snaps):
        role = "start" if k == 0 else "end" if k == len(result.snaps) - 1 else "via"
        note = f" ({s.note})" if s.note else ""
        typer.echo(
            f"stop {k} ({role}): node {s.node} at {s.lat:.5f}, {s.lon:.5f}, "
            f"moved {s.moved_m:.0f} m{note}"
        )
    typer.echo(f"baseline: {baseline.length_m / 1000:.2f} km, {_duration(baseline.time_s)}")
    typer.echo(f"worst: {route.metrics.length_m / 1000:.2f} km, {_duration(route.metrics.time_s)}")
    if baseline.length_m > 0:
        typer.echo(f"ratio: {route.metrics.length_m / baseline.length_m:.2f}x")
    else:
        typer.echo("ratio: n/a (a round trip's baseline is 0 m)")
    typer.echo(f"route: {len(route.nodes)} nodes, {len(route.edges)} edges")
    typer.echo(
        f"search: {run.attempts} attempts, {run.accepted} accepted, {run.elapsed_s:.1f} s, "
        f"stopped: {run.stopped_because}"
    )
    names = ", ".join(c.name for c in result.report.checks)
    typer.echo(f"validator: ok ({names})")
    if out is not None:
        out.write_text(json.dumps(route_document(data, result), indent=2) + "\n")
        typer.echo(f"wrote {out}")
    if geojson is not None:
        geojson.write_text(json.dumps(route_geojson(data, result)) + "\n")
        typer.echo(f"wrote {geojson}")


@app.command()
def validate(
    route_file: Annotated[
        Path, typer.Argument(help="A route.json written by plan.", exists=True, dir_okay=False)
    ],
    region: Annotated[str | None, typer.Option(help="Region (default: the file's).")] = None,
    build_id: Annotated[str | None, typer.Option(help="Build id (default: the file's).")] = None,
) -> None:
    """Re-check a saved route against its build, with its stops re-derived from its request."""
    from pydantic import ValidationError

    from worst_route.engine.io import read_route
    from worst_route.engine.request import request_masks
    from worst_route.engine.seed_route import resolve_stops
    from worst_route.engine.snap import StopUnreachable
    from worst_route.engine.validator import validate_route
    from worst_route.graph.python_backend import PythonBackend
    from worst_route.graph.spatial import SnapError, SpatialIndex

    try:
        saved = read_route(route_file)
    except (KeyError, TypeError, ValueError, OverflowError, ValidationError) as error:
        _fail(f"cannot read {route_file}: {type(error).__name__}: {error}")
    data = _load_build(region or saved.region, build_id or saved.build_id)
    if data.meta["graph_version"] != saved.graph_version:
        _fail(
            f"{route_file} was planned on graph_version {saved.graph_version[:12]}, "
            f"the build has {data.meta['graph_version'][:12]}"
        )
    spatial = SpatialIndex(data)
    masks = request_masks(spatial, data, saved.request)
    try:
        stops = resolve_stops(data, spatial, PythonBackend(data), saved.request, masks)
    except (SnapError, StopUnreachable) as error:
        _fail(f"{route_file}: its request cannot be planned on this build: {error}")
    report = validate_route(
        data,
        saved,
        stop_nodes=[s.node for s in stops],
        node_mask=masks.active,
        banned_edges=masks.banned,
    )
    typer.echo(f"{route_file}: {len(saved.nodes)} nodes on {saved.region}/{saved.build_id}")
    for line in report.lines():
        typer.echo(line)
    if not report.ok:
        raise typer.Exit(code=1)
    typer.echo("valid")
