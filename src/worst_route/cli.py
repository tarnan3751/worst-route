"""The `worst-route` command line.

`version` prints the package version. `pipeline <step>` runs one offline pipeline step for a
named region (`docs/PLAN.md` §2): download, filter, build, cleanup, enrich, tiers, save, qa.
Every step takes `--region` (a key of `config.REGIONS`), `--build-id` and `--force`; `filter`
and `build` also take `--source`, a file read instead of the step's usual input (fixtures,
tests). `download`, `filter` and `build` are real (`docs/reference/pipeline.md`): each prints
what it wrote and its counts, or that its output is up to date and was skipped; a failed
download prints one line and exits 1. Each imports its step module when it runs, so `version`
and the other commands do not load the geo stack. The other steps are a skeleton for now: each
exits with code 2 and says it is not implemented yet. `plan`, `validate` and `serve` arrive
with the engine and the API.
"""

from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from worst_route import __version__
from worst_route.config import REGIONS
from worst_route.pipeline import PIPELINE_VERSION, layout

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
