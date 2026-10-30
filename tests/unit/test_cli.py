import pytest
from typer.testing import CliRunner

from worst_route import __version__
from worst_route.cli import app

runner = CliRunner()

# The steps still to be built; download to save are tested in tests/pipeline/.
STUB_STEPS = ["qa"]


def test_version_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__ == "0.1.0"


def test_pipeline_qa_not_implemented() -> None:
    result = runner.invoke(app, ["pipeline", "qa", "--region", "portland"])
    assert result.exit_code == 2
    assert result.stderr.strip() == "not implemented yet: pipeline qa"
    assert result.stdout == ""


@pytest.mark.parametrize("step", STUB_STEPS)
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
