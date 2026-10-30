"""The `worst-route` command line.

`version` prints the package version. `pipeline <step>` runs one offline pipeline step for a
named region (`docs/PLAN.md` §2): download, filter, build, cleanup, enrich, tiers, save, qa.
Every step takes `--region` (a key of `config.REGIONS`), `--build-id` and `--force`. The steps
are a skeleton for now: each exits with code 2 and says it is not implemented yet. `plan`,
`validate` and `serve` arrive with the engine and the API.
"""

from typing import Annotated, NoReturn

import typer

from worst_route import __version__
from worst_route.config import REGIONS

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


def _not_implemented(step: str) -> NoReturn:
    typer.echo(f"not implemented yet: pipeline {step}", err=True)
    raise typer.Exit(code=2)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@pipeline_app.command()
def download(
    region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False
) -> None:
    """Fetch the region's Geofabrik state extract."""
    _not_implemented("download")


@pipeline_app.command("filter")
def filter_(
    region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False
) -> None:
    """Keep the drivable roads and the POIs; clip to the region's bbox."""
    _not_implemented("filter")


@pipeline_app.command()
def build(region: RegionOption, build_id: BuildIdOption = None, force: ForceOption = False) -> None:
    """Turn the filtered extract into a raw directed graph."""
    _not_implemented("build")


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
