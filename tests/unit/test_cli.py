from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from worst_route import __version__
from worst_route.cli import app
from worst_route.config import settings
from worst_route.graph.store import write_build
from worst_route.graph.testing import grid_graph

from ..engine.graphs import lattice

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


def test_serve_without_a_build_is_one_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    result = runner.invoke(app, ["serve", "--region", "portland"])
    settings.cache_clear()
    assert result.exit_code == 1
    assert result.stderr == (
        f"no build of portland under {tmp_path}: run `worst-route pipeline save --region "
        "portland` first\n"
    )


def test_serve_runs_the_app_for_its_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    g = grid_graph(3, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    write_build(g, tmp_path / "graphs" / "portland" / "b1")
    ran: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: ran.update(app=application, **kw))
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    args = ["serve", "--region", "portland", "--build-id", "b1", "--port", "0", "--workers", "3"]
    result = runner.invoke(app, [*args, "--backend", "python", "--store", str(tmp_path / "r.db")])
    settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert (ran["host"], ran["port"]) == ("127.0.0.1", 0)
    assert ran["log_config"]["loggers"]["worst_route"]["level"] == "INFO"
    api = ran["app"].state.api
    assert api.settings.data_dir == tmp_path and api.settings.build_id == "b1"
    assert api.jobs.settings.max_jobs == 3 and api.store.path == tmp_path / "r.db"
    assert api.builds["portland"].backend.name == "python"
    assert runner.invoke(app, [*args[:3], "--workers", "0"]).exit_code == 2


def test_serve_refuses_csr_on_a_build_with_turn_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    g = lattice(3, 3, transitions=[((0, 1), (1, 2))])
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    write_build(g, tmp_path / "graphs" / "portland" / "b1")
    (tmp_path / "graphs" / "portland" / "latest").write_text("b1\n")
    ran: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: ran.update(app=application))
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    refused = runner.invoke(app, ["serve", "--region", "portland", "--backend", "csr"])
    assert refused.exit_code == 1 and not ran  # uvicorn never started
    served = runner.invoke(app, ["serve", "--region", "portland", "--store", str(tmp_path / "r")])
    settings.cache_clear()
    assert refused.stderr == (
        "cannot serve: the csr backend cannot search turn tables yet; use --backend python\n"
    )
    assert served.exit_code == 0, served.output
    assert served.stdout == "backend: python (csr cannot search turn tables yet)\n"
    assert ran["app"].state.api.builds["portland"].backend.name == "python"
