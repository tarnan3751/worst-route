from pathlib import Path

import pytest
from typer.testing import CliRunner

from worst_route import __version__
from worst_route.cli import app
from worst_route.config import settings

runner = CliRunner()

# Every step but download (which would fetch with --force) on an empty data directory: the
# shared options parse and the step stops at its missing input.
STEPS = ["filter", "build", "cleanup", "enrich", "tiers", "save", "qa"]


def test_version_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__ == "0.1.0"


@pytest.mark.parametrize("step", STEPS)
def test_every_step_takes_the_shared_options(
    step: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    args = ["pipeline", step, "--region", "oregon", "--build-id", "b1", "--force"]
    result = runner.invoke(app, args)
    settings.cache_clear()
    assert result.exit_code == 1
    assert result.stderr.startswith(f"missing input {tmp_path}/")


def test_unknown_region_rejected() -> None:
    result = runner.invoke(app, ["pipeline", "download", "--region", "nowhere"])
    assert result.exit_code != 0
    assert "nowhere" in result.output


def test_region_required() -> None:
    result = runner.invoke(app, ["pipeline", "download"])
    assert result.exit_code != 0
