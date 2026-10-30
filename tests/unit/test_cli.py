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
from ..geocode.support import offline_clients

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
    offline_clients(monkeypatch)  # the default geocoder's client, on a MockTransport
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


def test_serve_takes_csr_on_a_build_with_turn_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    g = lattice(3, 3, transitions=[((0, 1), (1, 2))])
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    write_build(g, tmp_path / "graphs" / "portland" / "b1")
    (tmp_path / "graphs" / "portland" / "latest").write_text("b1\n")
    ran: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: ran.update(app=application))
    offline_clients(monkeypatch)
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    monkeypatch.delenv("WORST_ROUTE_CONTACT", raising=False)
    settings.cache_clear()
    served = runner.invoke(app, ["serve", "--region", "portland", "--store", str(tmp_path / "r")])
    settings.cache_clear()
    assert served.exit_code == 0, served.output
    assert served.stdout == (
        "backend: csr\n"
        "geocoder: nominatim https://nominatim.openstreetmap.org as "
        '"worst-route/0.1.0 (https://github.com/tarnan3751/worst-route)"\n'
    )
    assert ran["app"].state.api.builds["portland"].backend.name == "csr"


def test_serve_takes_the_geocoder_s_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    from worst_route.geocode.nominatim import NominatimGeocoder

    g = grid_graph(3, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    write_build(g, tmp_path / "graphs" / "portland" / "b1")
    (tmp_path / "graphs" / "portland" / "latest").write_text("b1\n")
    ran: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: ran.update(app=application))
    made = offline_clients(monkeypatch)
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    monkeypatch.setenv("WORST_ROUTE_CONTACT", "ops@example.org")
    settings.cache_clear()
    store = tmp_path / "lane" / "routes.sqlite"
    base = ["serve", "--region", "portland", "--backend", "python", "--store", str(store)]
    served = runner.invoke(app, [*base, "--nominatim-url", "http://127.0.0.1:9"])
    assert served.exit_code == 0, served.output
    assert served.stdout.splitlines()[-1] == (
        "geocoder: nominatim http://127.0.0.1:9 as "
        '"worst-route/0.1.0 (https://github.com/tarnan3751/worst-route; ops@example.org)"'
    )
    geocode = ran["app"].state.api.geocode
    assert isinstance(geocode.geocoder, NominatimGeocoder)
    assert geocode.geocoder.base_url == "http://127.0.0.1:9"
    assert geocode.cache.path == tmp_path / "lane" / "geocode.sqlite"
    assert made == [geocode.geocoder.client]
    off = runner.invoke(app, [*base, "--geocoder", "none"])
    assert off.stdout.splitlines()[-1] == "geocoder: none"
    assert ran["app"].state.api.geocode is None
    assert runner.invoke(app, [*base, "--geocoder", "photon"]).exit_code == 2
    settings.cache_clear()
