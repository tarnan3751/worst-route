import pytest
from typer.testing import CliRunner

from worst_route import __version__
from worst_route.cli import app

runner = CliRunner()

STEPS = ["download", "filter", "build", "cleanup", "enrich", "tiers", "save", "qa"]


def test_version_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__ == "0.1.0"


def test_pipeline_download_not_implemented() -> None:
    result = runner.invoke(app, ["pipeline", "download", "--region", "portland"])
    assert result.exit_code == 2
    assert result.stderr.strip() == "not implemented yet: pipeline download"
    assert result.stdout == ""


@pytest.mark.parametrize("step", STEPS)
def test_every_step_takes_the_shared_options(step: str) -> None:
    args = ["pipeline", step, "--region", "oregon", "--build-id", "b1", "--force"]
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert result.stderr.strip() == f"not implemented yet: pipeline {step}"


def test_unknown_region_rejected() -> None:
    result = runner.invoke(app, ["pipeline", "download", "--region", "nowhere"])
    assert result.exit_code != 0
    assert "nowhere" in result.output
    assert "not implemented" not in result.output


def test_region_required() -> None:
    result = runner.invoke(app, ["pipeline", "download"])
    assert result.exit_code != 0
