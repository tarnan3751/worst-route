"""The `worst-route` command line.

`version` prints the package version. `pipeline <step>` runs one offline pipeline step for a
named region (`docs/PLAN.md` §2): download, filter, build, cleanup, enrich, tiers, save, qa.
Every step takes `--region` (a key of `config.REGIONS`), `--build-id` and `--force`; `filter`
and `build` also take `--source`, a file read instead of the step's usual input (fixtures,
tests); `cleanup` and `tiers` also take `--allow-fragmented`. Each step
(`docs/reference/pipeline.md`) prints what it wrote and its counts, or that its output is up to
date and was skipped; a missing input, a failed download, a cleanup or tiers step whose graph
is too fragmented, a step whose earlier stages are stale (`layout.stale_stages`), or a save that
cannot write its build prints one line and exits 1. The steps from `cleanup` on read the build
named by `--build-id`, else the region's latest. `save` writes the build beside the stages and
never overwrites one without `--force`; `qa` prints its thresholds, also when it skips, and exits
1 when one fails. Each imports its step module when it runs, so `version` and the other commands
do not load the geo stack.

`plan` plans a route on a saved build (`docs/reference/engine.md`, **The CLI**), on the
`--objective` (distance or time), with `--no-left` or `--no-right` (such turns cost a kilometre
each; none with `--strict`): it prints the objective and constraints when not the default, the
snapped stops, the baseline, the worst route, the ratio and the time ratio (of the experienced
time, with its drive and delays), its turns and delays, the target and what was achieved (for
`--target ratio:<x>` or `duration:<time>`), the search, the backend it searched with and the
validator's report, with a progress line on stderr on an accepted splice, at most one every
2 s, and writes `route.json` and GeoJSON when asked; a stop that cannot be snapped or reached,
or a missing build (or one without an array of the format, which names its remedy: the rebuild
from `filter` when another pipeline version wrote it, which a plain `save` names too;
`validate`, `serve` and `qa` say the same), is one line and exit 1, and a route that fails the
validator is never printed. `--base route.json` continues a saved route ("make it worse",
toward the new target): its stops, tier, leash, objective and constraints come from the file,
which must have been planned on the build at its graph_version by this engine version.
`validate` re-checks a saved `route.json` against the build it names, with its stops re-derived
from its request (`resolve_stops`), and exits 1 when a check fails; a file made by another
engine version is one line and exit 1 (its stops could be re-derived differently).
Both take `--backend auto|csr|python` (`config.DEFAULT_BACKEND`, `auto`: csr when numba
imports) and print the one used, which changes only the speed; `csr` when numba does not import
is one line and exit 1.

`serve` runs the API (`docs/reference/api.md`) with uvicorn, in one process (jobs live in its
memory; each runs in a worker process of its own, `--workers` at once): it loads the region's
build first, so a missing one is one line and exit 1, and keeps its routes in `--store`
(default `<WORST_ROUTE_DATA>/routes.sqlite`). It takes `--backend` and prints the one used as
`plan` does. `--port 0` binds a free port, which uvicorn logs, and the job manager's lines (a
job cancelled, a worker failed) go to the same log. `--geocoder nominatim|none` and
`--nominatim-url` set the geocoding proxy (`docs/reference/api.md`, **Geocoding**), whose
cache is `geocode.sqlite` beside the store; it prints the geocoder and the User-Agent it sends,
the contact from `WORST_ROUTE_CONTACT` included.

`aux places` writes the places index (`docs/reference/presentation.md`, **Places**) under
`<WORST_ROUTE_DATA>/aux/places/` from the Census files `tools/fetch_census.sh` fetched into
`--census-dir` (default `<WORST_ROUTE_DATA>/aux/census/`): the states, their outlines, and the
places of every state's file present that meet the region's bbox (its latest build's
`params.bbox`, else `config.REGIONS`'s). It prints what it wrote, with a warning line on stderr
for each state the bbox meets whose places file is absent; a missing state file is one line and
exit 1.

`directions` prints a saved `route.json`'s turn-by-turn directions
(`docs/reference/presentation.md`, **Directions**), one numbered line a step, a driving step with
its distance and time, with the route's seed's jokes unless `--no-jokes`. It loads the build the
file names (or `--region` / `--build-id`), as `validate` does; a file it cannot read, or a build
at another graph_version, is one line and exit 1.
"""

import json
import re
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn, cast, get_args

import typer

from worst_route import __version__
from worst_route.config import (
    API_MAX_JOBS,
    DEFAULT_BACKEND,
    DEFAULT_BUDGET_S,
    DEFAULT_MAX_ITERATIONS,
    NOMINATIM_URL,
    REGIONS,
    TARGET_TOLERANCE,
    BackendName,
    GeocoderName,
    settings,
)
from worst_route.pipeline import PIPELINE_VERSION, layout

if TYPE_CHECKING:
    from worst_route.engine.request import RouteRequest
    from worst_route.graph.backend import GraphBackend
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


def _require(path: Path, hint: str) -> None:
    if not path.exists():
        typer.echo(f"missing input {path}: {hint}", err=True)
        raise typer.Exit(code=1)


def _build_of(region: str, build_id: str | None) -> tuple[str, Path]:
    """The build `--build-id` names, else the region's latest, and its directory."""
    if build_id is None:
        latest = layout.latest_file(region)
        _require(latest, f"run `worst-route pipeline build --region {region}` first")
        build_id = latest.read_text().strip()
    return build_id, layout.build_dir(region, build_id)


def _require_fresh(region: str, directory: Path, step: str) -> None:
    """Exit 1, naming the step to rerun, when a stage before `step` is stale."""
    stale = layout.stale_stages(directory, step)
    if stale:
        typer.echo(
            f"stage {stale[0]} is stale: rerun `worst-route pipeline {stale[0]} --region {region}`",
            err=True,
        )
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
    region: RegionOption,
    build_id: BuildIdOption = None,
    force: ForceOption = False,
    allow_fragmented: Annotated[
        bool,
        typer.Option(
            "--allow-fragmented",
            help="Keep the largest strongly connected component even when it holds fewer "
            "than SCC_MIN_RETAINED of the nodes.",
        ),
    ] = False,
) -> None:
    """Simplification, consolidation, carriageway twins, connectivity."""
    import pickle

    from worst_route.pipeline import cleanup as step

    hint = f"run `worst-route pipeline build --region {region}` first"
    if build_id is None:
        _require(layout.latest_file(region), hint)
        build_id = layout.latest_file(region).read_text().strip()
    directory = layout.build_dir(region, build_id)
    source = layout.stage_file(directory, "build.pkl")
    _require(source, hint)
    output = layout.stage_file(directory, "cleanup.pkl")
    record: dict[str, Any] = {
        "step": "cleanup",
        "inputs": _inputs(source),
        "params": {
            "allow_fragmented": allow_fragmented,
            "config": step.parameters(),
            "pipeline_version": PIPELINE_VERSION,
        },
    }
    if not force and layout.sidecar_matches(output, record):
        typer.echo(
            f"skipped: cleanup of build {build_id} is up to date at {output} (--force to redo)"
        )
        return
    layout.sidecar(output).unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    layout.stage_file(directory, "cleanup.json").unlink(missing_ok=True)
    with source.open("rb") as f:
        raw = pickle.load(f)
    try:
        graph, report = step.run_cleanup(raw, allow_fragmented=allow_fragmented)
    except step.FragmentedGraphError as error:
        typer.echo(f"cleanup failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    step.save_stage(graph, report, directory)
    layout.write_sidecar(output, record)
    simplified, a, b, twins, scc = (
        report.simplify,
        report.stage_a,
        report.stage_b,
        report.twins,
        report.connectivity,
    )
    coverage = "n/a" if twins.coverage is None else f"{twins.coverage:.3f}"
    typer.echo(f"cleanup {build_id}: wrote {output} ({_size(output)})")
    typer.echo(
        f"simplify: dropped {simplified.self_loops_dropped} self-loops, "
        f"removed {simplified.nodes_removed} nodes"
    )
    typer.echo(f"stage A: {a.clusters} clusters from {a.nodes_merged} nodes")
    typer.echo(
        f"twins: {twins.paired} of {twins.candidates} candidate edges paired; "
        f"motorway and trunk coverage {coverage}"
    )
    kinds = ", ".join(f"{kind} {n}" for kind, n in b.superjunctions.items())
    typer.echo(
        f"stage B: {sum(b.superjunctions.values())} super-junctions ({kinds}), "
        f"{b.internal_edges} internal edges, {b.twin_pairs} twin pairs carried"
    )
    typer.echo(
        f"connectivity: kept {scc.nodes_after} of {scc.nodes_before} nodes; dropped "
        f"{scc.dropped_nodes} nodes and {scc.dropped_length_m / 1000:.1f} km "
        f"in {scc.dropped_components} components"
    )
    typer.echo(f"nodes {graph.number_of_nodes()}, edges {graph.number_of_edges()}")


@pipeline_app.command()
def enrich(
    region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False
) -> None:
    """Speeds, identities, road units, flags, bearings, collectibles, delays, pass-through."""
    import pickle

    import pyarrow.parquet as pq

    from worst_route.pipeline import enrich as step

    build_id, directory = _build_of(region, build_id)
    source = layout.stage_file(directory, "cleanup.pkl")
    _require(source, f"run `worst-route pipeline cleanup --region {region}` first")
    _require_fresh(region, directory, "enrich")
    pois = layout.pois_parquet(region)
    _require(pois, f"run `worst-route pipeline filter --region {region}` first")
    output = layout.stage_file(directory, "enrich.pkl")
    record: dict[str, Any] = {
        "step": "enrich",
        "inputs": _inputs(source) + _inputs(pois),
        "params": {"config": step.parameters(), "pipeline_version": PIPELINE_VERSION},
    }
    if not force and layout.sidecar_matches(output, record):
        typer.echo(
            f"skipped: enrich of build {build_id} is up to date at {output} (--force to redo)"
        )
        return
    layout.sidecar(output).unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    layout.stage_file(directory, "enrich.json").unlink(missing_ok=True)
    with source.open("rb") as f:
        graph = pickle.load(f)
    report = step.run_enrich(graph, pq.read_table(pois))
    step.save_stage(graph, report, directory)
    layout.write_sidecar(output, record)
    typer.echo(f"enrich {build_id}: wrote {output} ({_size(output)})")
    parsed = sum(report.speed_parsed.values())
    typer.echo(
        f"speeds: {parsed} edges from maxspeed, {report.edges - parsed} at their class default"
    )
    typer.echo(
        f"identities: {report.identities} on {report.edges_with_identity} edges; "
        f"units: {report.units} ({report.two_way_pairs} two-way pairs)"
    )
    typer.echo("flags: " + ", ".join(f"{flag} {n}" for flag, n in report.flags.items()))
    for kind, c in report.collectibles.items():
        typer.echo(
            f"{kind}: {c['found']} found, {c['on_edges']} on edges, {c['at_junctions']} at "
            f"junctions, {c['off_graph']} off the graph; on {c['edges']} edges"
        )
    for kind, d in report.delays.items():
        typer.echo(
            f"{kind}: {d['found']} found, {d['at_junctions']} at junctions (a delay), "
            f"{d['shape_points']} shape points (none), {d['off_graph']} off the graph"
        )
    typer.echo(
        f"nodes: {report.delay_nodes} with a delay, {report.pass_through} of {report.nodes} "
        "pass-through"
    )


@pipeline_app.command()
def tiers(
    region: RegionOption,
    build_id: BuildIdOption = None,
    force: ForceOption = False,
    allow_fragmented: Annotated[
        bool,
        typer.Option(
            "--allow-fragmented",
            help="Keep each tier's largest strongly connected component even when it holds "
            "fewer than TIER_MIN_RETAINED of the tier's nodes.",
        ),
    ] = False,
) -> None:
    """Each tier's nodes: the largest strongly connected component of its roads."""
    import pickle

    from worst_route.pipeline import tiers as step

    build_id, directory = _build_of(region, build_id)
    source = layout.stage_file(directory, "enrich.pkl")
    _require(source, f"run `worst-route pipeline enrich --region {region}` first")
    _require_fresh(region, directory, "tiers")
    output = layout.stage_file(directory, "tiers.json")
    record: dict[str, Any] = {
        "step": "tiers",
        "inputs": _inputs(source),
        "params": {
            "allow_fragmented": allow_fragmented,
            "config": step.parameters(),
            "pipeline_version": PIPELINE_VERSION,
        },
    }
    if not force and layout.sidecar_matches(output, record):
        typer.echo(
            f"skipped: tiers of build {build_id} are up to date at {output} (--force to redo)"
        )
        return
    layout.sidecar(output).unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    with source.open("rb") as f:
        graph = pickle.load(f)
    try:
        report = step.run_tiers(graph, allow_fragmented=allow_fragmented)
    except step.FragmentedTierError as error:
        typer.echo(f"tiers failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    step.save_stage(report, directory)
    layout.write_sidecar(output, record)
    typer.echo(f"tiers {build_id}: wrote {output} ({_size(output)})")
    for t in report.tiers:
        typer.echo(
            f"{t.name}: kept {t.kept_nodes} of {t.nodes} nodes ({t.retained:.2%}) and "
            f"{t.kept_edges} of {t.edges} edges; {t.components} components"
        )


@pipeline_app.command()
def save(region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False) -> None:
    """Write the build in the on-disk format, beside its stages."""
    import json
    import pickle

    from worst_route.graph.store import outdated
    from worst_route.pipeline import save as step

    build_id, directory = _build_of(region, build_id)
    spec = REGIONS[region]
    source = layout.stage_file(directory, "enrich.pkl")
    tiers_json = layout.stage_file(directory, "tiers.json")
    reads = [  # (file, the step that writes it)
        (source, "enrich"),
        (tiers_json, "tiers"),
        (layout.sidecar(layout.stage_file(directory, "build.pkl")), "build"),
        *((layout.sidecar(layout.stage_file(directory, out)), s) for s, out in step.STEP_OUTPUTS),
        (layout.roads_pbf(region), "filter"),
        (layout.sidecar(layout.roads_pbf(region)), "filter"),
    ]
    for path, writer in reads:
        _require(path, f"run `worst-route pipeline {writer} --region {region}` first")
    _require_fresh(region, directory, "save")
    try:
        sources = step.build_sources(spec, directory)
    except step.SourcesChangedError as error:
        _fail(f"save failed: {error}")
    params = step.build_params(spec, directory)
    output = layout.stage_file(directory, "save.json")
    record: dict[str, Any] = {
        "step": "save",
        "inputs": _inputs(source) + _inputs(tiers_json),
        "params": {"meta": params, "pipeline_version": PIPELINE_VERSION},
    }
    if not force and (directory / "meta.json").exists():
        # a build another pipeline version wrote is rebuilt from filter, not saved over
        line = outdated(directory, json.loads((directory / "meta.json").read_text()))
        if line is not None:
            _fail(line)
        if layout.sidecar_matches(output, record):
            typer.echo(f"skipped: build {build_id} is written at {directory} (--force to redo)")
            return
        _fail(
            f"save failed: build {build_id} is already written at {directory} from other "
            "inputs; a build is never overwritten (--force to replace it)"
        )
    step.remove_build(directory)
    layout.sidecar(output).unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    with source.open("rb") as f:
        graph = pickle.load(f)
    tiers = json.loads(tiers_json.read_text())["tiers"]
    meta = step.build_meta(spec, build_id, params, sources)
    try:
        data = step.graph_data(graph, [t["node_ids"] for t in tiers], meta)
    except step.EmptyTransitionsError as error:
        _fail(f"save failed: {error}")
    report = step.save_build(data, directory)
    layout.write_sidecar(output, record)
    c = report.counts
    typer.echo(f"save {build_id}: wrote {directory} ({report.megabytes:.1f} MB)")
    typer.echo(f"graph_version {report.graph_version}")
    typer.echo(
        f"nodes {c['nodes']}, edges {c['edges']}; {c['twin_pairs']} twin pairs; "
        f"{c['transitions']} transitions at {c['superjunctions']} super-junctions"
    )


@pipeline_app.command()
def qa(region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False) -> None:
    """Write the build's QA report and layers and check its thresholds."""
    import json

    from worst_route.graph.store import MissingArray
    from worst_route.pipeline import qa as step

    build_id, directory = _build_of(region, build_id)
    inputs = [directory / "meta.json"] + [
        layout.stage_file(directory, name)
        for name in ("cleanup.json", "enrich.json", "enrich.pkl", "tiers.json", "save.json")
    ]
    writers = ("save", "cleanup", "enrich", "enrich", "tiers", "save")
    for path, writer in zip(inputs, writers, strict=True):
        _require(path, f"run `worst-route pipeline {writer} --region {region}` first")
    _require_fresh(region, directory, "qa")
    output = layout.qa_file(directory, "qa.json")
    record: dict[str, Any] = {
        "step": "qa",
        "inputs": [entry for path in inputs for entry in _inputs(path)],
        "params": {"config": step.parameters(), "pipeline_version": PIPELINE_VERSION},
    }
    if not force and layout.sidecar_matches(output, record):
        typer.echo(f"skipped: QA of build {build_id} is up to date at {output} (--force to redo)")
        report = json.loads(output.read_text())
        thresholds = [step.Threshold(**t) for t in report["thresholds"]]
    else:
        layout.sidecar(output).unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        try:
            result = step.run_qa(directory)
        except MissingArray as error:
            _fail(str(error))
        layout.write_sidecar(output, record)
        thresholds = result.thresholds
        typer.echo(f"qa {build_id}: wrote {output.parent} (qa.json and {len(step.LAYERS)} layers)")
    for t in thresholds:
        value = "n/a" if t.value is None else f"{t.value:.4f}"
        typer.echo(f"{t.name}: {value} (at least {t.minimum}): {'pass' if t.passed else 'FAIL'}")
    failed = [t.name for t in thresholds if not t.passed]
    if failed:
        _fail(f"qa failed: {', '.join(failed)}")
    typer.echo("qa passed")


PROGRESS_EVERY_S = 2.0
LatLonOption = Annotated[str, typer.Option(metavar="LAT,LON")]


def _check_backend(value: str) -> str:
    if value not in get_args(BackendName):
        known = ", ".join(get_args(BackendName))
        raise typer.BadParameter(f"unknown backend {value!r}; known: {known}")
    return value


BackendOption = Annotated[
    str,
    typer.Option(
        help="Graph backend: csr, python, or auto (csr when numba imports).",
        callback=_check_backend,
    ),
]


def _latlon(text: str) -> tuple[float, float]:
    try:
        lat, lon = (float(part) for part in text.split(","))
    except ValueError:
        raise typer.BadParameter(f"{text!r}: expected LAT,LON") from None
    return lat, lon


def _fail(message: str, code: int = 1) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


def _make_backend(data: "GraphData", name: str) -> "GraphBackend":
    from worst_route.graph.backends import make_backend

    try:
        return make_backend(data, cast(BackendName, name))
    except ImportError as error:  # only csr needs numba; auto falls back to python
        _fail(f"the csr backend needs numba, which did not import: {error}")


def _load_build(region: str, build_id: str | None) -> "GraphData":
    from worst_route.graph.store import MissingArray, read_build

    hint = f"run `worst-route pipeline save --region {region}` first"
    if build_id is None:
        latest = layout.latest_file(region)
        if not latest.exists():
            _fail(f"no build of {region}: {hint}")
        build_id = latest.read_text().strip()
    path = layout.build_dir(region, build_id)
    if not (path / "meta.json").exists():
        _fail(f"no saved build at {path}: {hint}")
    try:
        return read_build(path)
    except MissingArray as error:
        _fail(str(error))


def _duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min {secs} s"


TIME = re.compile(r"(?:(\d+(?:\.\d*)?)h)?(?:(\d+(?:\.\d*)?)m)?(?:(\d+(?:\.\d*)?)s)?")


def _seconds(text: str) -> float:
    """`9h`, `90m`, `2h30m`, `45s` or a number of seconds, in seconds."""
    try:
        return float(text)
    except ValueError:
        pass
    match = TIME.fullmatch(text)
    if not text or match is None:
        raise ValueError(text)
    hours, minutes, secs = (float(part) if part else 0.0 for part in match.groups())
    return hours * 3600 + minutes * 60 + secs


def _target(text: str) -> dict[str, Any]:
    """`--target`'s value as a request's target: max, ratio:<x> or duration:<time>."""
    kind, colon, value = text.partition(":")
    try:
        if kind == "max" and not colon:
            return {"kind": "max"}
        if kind == "ratio":
            return {"kind": "ratio", "value": float(value)}
        if kind == "duration":
            return {"kind": "duration", "value": _seconds(value)}
    except ValueError:
        pass
    raise typer.BadParameter(
        f"--target {text!r}: expected max, ratio:<x> or duration:<time> "
        "(9h, 90m, 2h30m, 45s or seconds)"
    )


def _target_line(kind: str, wanted: float, achieved: float, within: bool) -> str:
    """`target: ratio 20x → achieved 19.87x (within ±2 %)`, or the duration's."""
    if kind == "ratio":
        what, got = f"ratio {wanted:g}x", f"{achieved:.2f}x"
    else:
        what, got = f"duration {_duration(wanted)}", _duration(achieved)
    band = f"{'within' if within else 'outside'} ±{TARGET_TOLERANCE * 100:g} %"
    return f"target: {what} → achieved {got} ({band})"


def _mode_line(request: "RouteRequest") -> str:
    """`objective: time, few left turns` (or `no left turns` when strict)."""
    parts: list[str] = [request.objective]
    for kind in request.constraints.kinds:
        way = "left" if kind.key == "no_left" else "right"
        parts.append(f"{'no' if request.constraints.strict else 'few'} {way} turns")
    return "objective: " + ", ".join(parts)


@app.command()
def plan(
    ctx: typer.Context,
    region: RegionOption,
    from_: Annotated[
        str | None, typer.Option("--from", metavar="LAT,LON", help="The start.")
    ] = None,
    to: Annotated[str | None, typer.Option(metavar="LAT,LON", help="The end.")] = None,
    build_id: Annotated[
        str | None, typer.Option(help="Build id (default: latest, or the --base file's).")
    ] = None,
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
    backend: BackendOption = DEFAULT_BACKEND,
    target: Annotated[
        str, typer.Option(help="max, ratio:<x> (× the baseline) or duration:<time> (9h, 2h30m).")
    ] = "max",
    objective: Annotated[str, typer.Option(help="distance, time or collect.")] = "distance",
    item: Annotated[
        str | None,
        typer.Option(
            help="What --objective collect collects: toll_booth, level_crossing, roundabout or "
            "bridge."
        ),
    ] = None,
    no_left: Annotated[
        bool, typer.Option("--no-left", help="Left turns cost a kilometre (none, --strict).")
    ] = False,
    no_right: Annotated[
        bool, typer.Option("--no-right", help="Right turns cost a kilometre (none, --strict).")
    ] = False,
    strict: Annotated[
        bool, typer.Option("--strict", help="No such turn at all, or no route.")
    ] = False,
    base: Annotated[
        Path | None,
        typer.Option(
            help="Continue this route.json (its stops, tier, leash and objective).",
            exists=True,
            dir_okay=False,
        ),
    ] = None,
) -> None:
    """Plan the worst route between stops, validated."""
    from pydantic import ValidationError

    from worst_route.engine import ENGINE_VERSION
    from worst_route.engine.io import SavedRoute, read_route, route_document, route_geojson
    from worst_route.engine.objectives import OBJECTIVES
    from worst_route.engine.plan import BaseRouteMismatch, plan_route
    from worst_route.engine.request import RouteRequest, objective_problem
    from worst_route.engine.route import IllegalSplice
    from worst_route.engine.seed_route import Baseline
    from worst_route.engine.snap import InfeasibleTurnConstraint, StopUnreachable, is_round_trip
    from worst_route.engine.turns import count_turns
    from worst_route.engine.validator import ValidationFailed
    from worst_route.engine.worsen import ProgressEvent
    from worst_route.graph.spatial import SnapError

    fields: dict[str, Any] = {
        "budget_s": budget,
        "max_iterations": max_iterations,
        "seed": seed if seed is not None else secrets.randbelow(2**31),
        "target": _target(target),
        "objective": objective,
        "item": item,
        "constraints": {"no_left": no_left, "no_right": no_right, "strict": strict},
    }
    saved: SavedRoute | None = None
    if base is None:
        if from_ is None or to is None:
            _fail("--from and --to are needed, unless --base names a route", code=2)
        if strict and not (no_left or no_right):
            _fail("--strict needs --no-left or --no-right", code=2)
        fields["stops"] = [_latlon(from_), *(_latlon(v) for v in via or []), _latlon(to)]
        fields["tier"] = tier
        try:
            fields["leash_km"] = leash_km if leash_km == "auto" else float(leash_km)
        except ValueError as error:
            raise typer.BadParameter(f"--leash-km: {error}") from None
    else:
        given = [name for name, value in (("--from", from_), ("--to", to), ("--via", via)) if value]
        for name in ("tier", "leash_km"):
            source = ctx.get_parameter_source(name)
            if source is not None and source.name != "DEFAULT":
                given.append(f"--{name.replace('_', '-')}")
        if given:
            message = (
                f"--base takes the stops, tier and leash from its route: drop {', '.join(given)}"
            )
            _fail(message, code=2)
        mode = [
            f"--{name.replace('_', '-')}"
            for name in ("objective", "item", "no_left", "no_right", "strict")
            if (source := ctx.get_parameter_source(name)) is not None and source.name != "DEFAULT"
        ]
        if mode:
            message = (
                "--base takes the objective, item and constraints from its route: drop "
                f"{', '.join(mode)}"
            )
            _fail(message, code=2)
        try:
            saved = read_route(base)
        except (KeyError, TypeError, ValueError, OverflowError, ValidationError) as error:
            _fail(f"cannot read {base}: {type(error).__name__}: {error}")
        if saved.engine_version != ENGINE_VERSION:
            _fail(
                f"{base} was made by engine version {saved.engine_version}; "
                f"this is version {ENGINE_VERSION}: re-plan"
            )
        if saved.region != region:
            _fail(f"{base} was planned on {saved.region}, not {region}")
        build_id = build_id or saved.build_id
        fields.update(
            stops=saved.request.stops,
            objective=saved.request.objective,
            item=saved.request.item,
            constraints=saved.request.constraints,
            tier=saved.request.tier,
            leash_km=saved.request.leash_km,
        )
    if fields["target"]["kind"] == "ratio" and is_round_trip(fields["stops"]):
        _fail("invalid --target: a round trip has no ratio target", code=2)
    if fields["objective"] not in OBJECTIVES:  # before its item: the API refuses it first too
        unknown, known = fields["objective"], ", ".join(OBJECTIVES)
        _fail(f"invalid --objective: unknown objective {unknown!r}; known: {known}", code=2)
    problem = objective_problem(fields["objective"], fields["item"], fields["target"]["kind"])
    if problem is not None:
        _fail(f"invalid --{problem[0]}: {problem[1]}", code=2)
    try:
        request = RouteRequest(**fields)
    except ValidationError as error:
        problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]
        raise typer.BadParameter("; ".join(problems)) from None
    data = _load_build(region, build_id)
    if saved is not None and data.meta["graph_version"] != saved.graph_version:
        _fail(
            f"{base} was planned on graph_version {saved.graph_version[:12]}, "
            f"the build has {data.meta['graph_version'][:12]}"
        )
    engine = _make_backend(data, backend)

    last = time.monotonic()

    def progress(event: ProgressEvent, baseline: Baseline) -> None:
        nonlocal last
        if time.monotonic() - last >= PROGRESS_EVERY_S:
            last = time.monotonic()
            km = event.metrics.length_m / 1000
            ratio = event.metrics.length_m / baseline.length_m if baseline.length_m else 0.0
            typer.echo(f"iteration {event.iteration}: ratio {ratio:.2f}x, {km:.2f} km", err=True)

    try:
        result = plan_route(data, request, backend=engine, base=saved, on_progress=progress)
    except InfeasibleTurnConstraint as error:
        _fail(f"cannot plan: {error}; try without --strict")
    except (SnapError, StopUnreachable, BaseRouteMismatch) as error:
        _fail(f"cannot plan: {error}")
    except (IllegalSplice, IndexError) as error:
        if saved is None:  # not from a file: an engine bug, so the traceback
            raise
        _fail(f"cannot plan: {base} is not a legal route: {type(error).__name__}: {error}")
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
    if request.objective != "distance" or request.constraints.kinds:
        typer.echo(_mode_line(request))
    for k, s in enumerate(result.snaps):
        role = "start" if k == 0 else "end" if k == len(result.snaps) - 1 else "via"
        note = f" ({s.note})" if s.note else ""
        typer.echo(
            f"stop {k} ({role}): node {s.node} at {s.lat:.5f}, {s.lon:.5f}, "
            f"moved {s.moved_m:.0f} m{note}"
        )
    typer.echo(f"baseline: {baseline.length_m / 1000:.2f} km, {_duration(baseline.time_s)}")
    if saved is not None:
        km, was = saved.metrics.length_m / 1000, _duration(saved.metrics.time_s)
        ratio = f", {saved.metrics.length_m / baseline.length_m:.2f}x" if baseline.length_m else ""
        typer.echo(f"base: {base}: {km:.2f} km, {was}{ratio}")
    typer.echo(f"worst: {route.metrics.length_m / 1000:.2f} km, {_duration(route.metrics.time_s)}")
    if baseline.length_m > 0:
        typer.echo(f"ratio: {route.metrics.length_m / baseline.length_m:.2f}x")
    else:
        typer.echo("ratio: n/a (a round trip's baseline is 0 m)")
    if baseline.time_s > 0:  # the experienced time, driving and delays, as the API's
        drive, delays = route.metrics.time_s, route.metrics.delay_s
        experienced = (drive + delays) / (baseline.time_s + baseline.delay_s)
        breakdown = f"drive {_duration(drive)} + delays {_duration(delays)}"
        typer.echo(f"time ratio: {experienced:.2f}x ({breakdown} = {_duration(drive + delays)})")
    turns = count_turns(data, route.edges)
    typer.echo(
        f"turns: {turns['left']} left, {turns['right']} right, {turns['straight']} straight, "
        f"{turns['u_turn']} u-turns; delays {_duration(route.metrics.delay_s)}"
    )
    reached = result.goal.describe(result.goal.value(route.score, route.metrics))
    if reached is not None:
        typer.echo(
            _target_line(
                reached["kind"], reached["value"], reached["achieved"], reached["within_tolerance"]
            )
        )
    if "item" in route.metrics.counters:
        wanted, items = route.metrics.counters["item"], route.metrics.counters["items"]
        every = ", ".join(f"{name} {count}" for name, count in items.items())
        typer.echo(f"collected: {items[wanted]} {wanted} ({every})")
    typer.echo(f"route: {len(route.nodes)} nodes, {len(route.edges)} edges")
    typer.echo(
        f"search: {run.attempts} attempts, {run.accepted} accepted, {run.elapsed_s:.1f} s, "
        f"stopped: {run.stopped_because}"
    )
    typer.echo(f"backend: {result.backend}")
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
    backend: BackendOption = DEFAULT_BACKEND,
) -> None:
    """Re-check a saved route against its build, with its stops re-derived from its request."""
    from pydantic import ValidationError

    from worst_route.engine import ENGINE_VERSION
    from worst_route.engine.io import read_route
    from worst_route.engine.request import request_masks
    from worst_route.engine.seed_route import resolve_stops
    from worst_route.engine.snap import StopUnreachable
    from worst_route.engine.validator import validate_route
    from worst_route.graph.spatial import SnapError, SpatialIndex

    try:
        saved = read_route(route_file)
    except (KeyError, TypeError, ValueError, OverflowError, ValidationError) as error:
        _fail(f"cannot read {route_file}: {type(error).__name__}: {error}")
    if saved.engine_version != ENGINE_VERSION:  # its stops are re-derived with this engine
        _fail(
            f"{route_file} was made by engine version {saved.engine_version}; "
            f"this is version {ENGINE_VERSION}: re-plan"
        )
    data = _load_build(region or saved.region, build_id or saved.build_id)
    if data.meta["graph_version"] != saved.graph_version:
        _fail(
            f"{route_file} was planned on graph_version {saved.graph_version[:12]}, "
            f"the build has {data.meta['graph_version'][:12]}"
        )
    spatial = SpatialIndex(data)
    masks = request_masks(spatial, data, saved.request)
    engine = _make_backend(data, backend)
    try:
        stops = resolve_stops(data, spatial, engine, saved.request, masks)
    except (SnapError, StopUnreachable) as error:
        _fail(f"{route_file}: its request cannot be planned on this build: {error}")
    report = validate_route(
        data,
        saved,
        stop_nodes=[s.node for s in stops],
        node_mask=masks.active,
        banned_edges=masks.banned,
        banned_turn_classes=saved.request.constraints.banned_turn_classes,
    )
    typer.echo(f"{route_file}: {len(saved.nodes)} nodes on {saved.region}/{saved.build_id}")
    typer.echo(f"backend: {engine.name}")
    for line in report.lines():
        typer.echo(line)
    if not report.ok:
        raise typer.Exit(code=1)
    typer.echo("valid")


def _check_geocoder(value: str) -> str:
    if value not in get_args(GeocoderName):
        known = ", ".join(get_args(GeocoderName))
        raise typer.BadParameter(f"unknown geocoder {value!r}; known: {known}")
    return value


@app.command()
def serve(
    region: RegionOption,
    build_id: Annotated[str | None, typer.Option(help="Build id (default: latest).")] = None,
    host: Annotated[str, typer.Option(help="Address to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind (0: a free one).")] = 8000,
    workers: Annotated[
        int, typer.Option(min=1, help="Jobs running at once, each in a worker process.")
    ] = API_MAX_JOBS,
    backend: BackendOption = DEFAULT_BACKEND,
    store: Annotated[
        Path | None, typer.Option(help="The route store (default: <data>/routes.sqlite).")
    ] = None,
    geocoder: Annotated[
        str,
        typer.Option(
            help="The place search's geocoder: nominatim, or none (no search).",
            callback=_check_geocoder,
        ),
    ] = "nominatim",
    nominatim_url: Annotated[str, typer.Option(help="Nominatim's address.")] = NOMINATIM_URL,
) -> None:
    """Serve the API."""
    import copy

    import uvicorn

    from worst_route.api.app import ApiSettings, create_app, geocoder_user_agent
    from worst_route.api.jobs import JobSettings
    from worst_route.graph.loader import BuildNotFound

    config = ApiSettings(
        data_dir=settings().data_dir,
        region=region,
        build_id=build_id or "latest",
        backend=cast(BackendName, backend),
        store_path=store,
        jobs=JobSettings(max_jobs=workers),
        geocoder=cast(GeocoderName, geocoder),
        nominatim_url=nominatim_url,
        contact=settings().contact,
    )
    try:
        application = create_app(config)
    except BuildNotFound as error:
        _fail(str(error))
    except ImportError as error:  # only csr needs numba; auto falls back to python
        _fail(f"the csr backend needs numba, which did not import: {error}")
    loaded = application.state.api.builds[region]
    typer.echo(f"backend: {loaded.backend.name}")
    if config.geocoder == "none":
        typer.echo("geocoder: none")
    else:
        typer.echo(f'geocoder: nominatim {nominatim_url} as "{geocoder_user_agent(config)}"')
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["loggers"]["worst_route"] = {"handlers": ["default"], "level": "INFO"}
    uvicorn.run(application, host=host, port=port, log_config=log_config)


aux_app = typer.Typer(help="Data beside the builds: the Census places index.", no_args_is_help=True)
app.add_typer(aux_app, name="aux")


def _region_bbox(region: str) -> tuple[float, float, float, float] | None:
    """The region's latest build's bbox (`params.bbox`), else its own in `config.REGIONS`."""
    latest = layout.latest_file(region)
    if latest.exists():
        meta = layout.build_dir(region, latest.read_text().strip()) / "meta.json"
        if meta.exists():
            bbox = json.loads(meta.read_text())["params"].get("bbox")
            if bbox is not None:
                west, south, east, north = (float(v) for v in bbox)
                return west, south, east, north
    return REGIONS[region].bbox


@aux_app.command("places")
def aux_places(
    region: RegionOption,
    census_dir: Annotated[
        Path | None,
        typer.Option(help="The Census files (default: <data>/aux/census).", file_okay=False),
    ] = None,
) -> None:
    """Write the places index (states, outlines, towns) from the Census files."""
    from worst_route.presentation import places

    directory = census_dir or places.census_dir()
    states, outlines = places.state_files(directory)
    for path in (states, outlines):
        _require(path, "run `tools/fetch_census.sh` first")
    written = places.write_places(
        region,
        _region_bbox(region),
        states=states,
        outlines=outlines,
        places=places.place_files(directory),
        out_dir=places.places_dir(),
    )
    typer.echo(f"wrote {written.states} ({written.n_states} states)")
    typer.echo(f"wrote {written.outlines} ({written.n_outlines} outlines)")
    for state in written.missing:
        typer.echo(
            f"missing places for {state.usps}: run tools/fetch_census.sh {state.fips}", err=True
        )
    by_state = ", ".join(f"{usps} {n}" for usps, n in written.by_state.items())
    total = sum(written.by_state.values())
    typer.echo(f"wrote {written.places} ({total} places{': ' + by_state if by_state else ''})")


@app.command()
def directions(
    route_file: Annotated[
        Path, typer.Argument(help="A route.json written by plan.", exists=True, dir_okay=False)
    ],
    region: Annotated[str | None, typer.Option(help="Region (default: the file's).")] = None,
    build_id: Annotated[str | None, typer.Option(help="Build id (default: the file's).")] = None,
    jokes: Annotated[bool, typer.Option(help="Tell the route's jokes.")] = True,
) -> None:
    """Print a saved route's turn-by-turn directions."""
    from pydantic import ValidationError

    from worst_route.engine.io import read_route
    from worst_route.presentation.document import route_directions

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
    steps = route_directions(
        data,
        saved.nodes,
        saved.edges,
        saved.stop_idx,
        labels=[None] * len(saved.stop_idx),
        seed=saved.seed,
        ratio=saved.document["metrics"]["ratio"],
        jokes=jokes,
    )
    width = len(str(len(steps)))
    for step in steps:
        line = f"{step.step:>{width}}. {step.text}"
        if step.edge_range[0] < step.edge_range[1]:
            line += f"  ({step.distance_m / 1000:.2f} km, {_duration(step.time_s)})"
        typer.echo(line)
