from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from worst_route import __version__
from worst_route.cli import app
from worst_route.config import PIPELINE_VERSION, settings
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


def test_serve_on_a_build_saved_before_an_array_existed_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    g = grid_graph(3, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1", "pipeline_version": "1"})
    path = tmp_path / "graphs" / "portland" / "b1"
    write_build(g, path)
    (path / "arrays" / "node_roundabouts.npy").unlink()
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    result = runner.invoke(app, ["serve", "--region", "portland", "--build-id", "b1"])
    settings.cache_clear()
    assert result.exit_code == 1
    assert result.stderr == (
        f"the build at {path} was written by pipeline 1 (now {PIPELINE_VERSION}): "
        "rebuild the region with `worst-route pipeline filter --region portland` and the steps "
        "after it\n"
    )


def test_serve_on_a_build_without_its_geometry_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A build that plans but cannot draw a route: serve refuses before it listens.
    g = grid_graph(3, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    path = tmp_path / "graphs" / "portland" / "b1"
    write_build(g, path)
    (path / "geometry" / "geom_dxy.npy").unlink()
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    result = runner.invoke(app, ["serve", "--region", "portland", "--build-id", "b1"])
    settings.cache_clear()
    assert result.exit_code == 1
    assert result.stderr == (
        f"the build at {path} lacks its geometry (geom_dxy): rebuild it with "
        "`worst-route pipeline save --force --region portland`\n"
    )


@pytest.mark.parametrize(
    ("name", "refusal"),
    [
        (  # format 1's parquet files, which this version no longer reads
            "portland.parquet",
            "the places index of portland in {places} is at format 1 (parquet), and this version "
            "reads format 2: run `worst-route aux places --region portland` again\n",
        ),
        (  # an empty manifest
            "places.json",
            "{places}/places.json is malformed: delete it, then run `worst-route aux places "
            "--region portland` again, and for each other region whose index is there\n",
        ),
    ],
)
def test_serve_on_a_places_index_it_cannot_read_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, refusal: str
) -> None:
    # Serve refuses before it listens, and before it opens the route store.
    g = grid_graph(3, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    write_build(g, tmp_path / "graphs" / "portland" / "b1")
    places = tmp_path / "aux" / "places"
    places.mkdir(parents=True)
    (places / name).write_bytes(b"PAR1" if name.endswith(".parquet") else b"")
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    args = ["serve", "--region", "portland", "--build-id", "b1", "--store", str(tmp_path / "r")]
    result = runner.invoke(app, [*args, "--backend", "python", "--geocoder", "none"])
    settings.cache_clear()
    assert result.exit_code == 1
    assert result.stderr == refusal.format(places=places)
    assert not (tmp_path / "r").exists()


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
    assert api.settings.data_dir == tmp_path and api.settings.builds == (("portland", "b1"),)
    assert api.jobs.settings.max_jobs == 3 and api.store.path == tmp_path / "r.db"
    assert api.builds["portland"].backend.name == "python"
    assert runner.invoke(app, [*args[:3], "--workers", "0"]).exit_code == 2


def test_serve_with_csr_and_a_broken_numba_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken_numba: None
) -> None:
    import uvicorn

    g = grid_graph(3, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    write_build(g, tmp_path / "graphs" / "portland" / "b1")
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: None)  # never a real server
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    args = ["serve", "--region", "portland", "--build-id", "b1", "--backend", "csr"]
    result = runner.invoke(app, [*args, "--geocoder", "none", "--store", str(tmp_path / "r")])
    settings.cache_clear()
    assert result.exit_code == 1
    assert result.stderr == (
        "the csr backend needs numba, which did not import: numba needs another NumPy\n"
    )


def test_serve_with_auto_and_a_broken_numba_takes_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken_numba: None
) -> None:
    import uvicorn

    g = grid_graph(3, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    write_build(g, tmp_path / "graphs" / "portland" / "b1")
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: None)
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    args = ["serve", "--region", "portland", "--build-id", "b1", "--geocoder", "none"]
    result = runner.invoke(app, [*args, "--store", str(tmp_path / "r")])
    settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "backend: python"  # as make_backend will make


def test_serve_listens_before_it_makes_the_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a cold cache, making the CSR backend compiles the kernels: uvicorn gets the app first.
    import uvicorn

    g = grid_graph(3, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    write_build(g, tmp_path / "graphs" / "portland" / "b1")
    made: list[bool] = []

    def run(application: Any, **kw: Any) -> None:
        loaded = application.state.api.builds["portland"]
        made.append("backend" in vars(loaded) or "spatial" in vars(loaded))

    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    args = ["serve", "--region", "portland", "--build-id", "b1", "--geocoder", "none"]
    result = runner.invoke(app, [*args, "--store", str(tmp_path / "r")])
    settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "backend: csr"  # auto, named and not made
    assert made == [False]


def test_serve_takes_several_regions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    for region, build_id in (("oregon", "b1"), ("oregon", "b2"), ("portland", "b1")):
        g = grid_graph(3, 3, 100)
        g = replace(g, meta={**g.meta, "region": region, "build_id": build_id})
        write_build(g, tmp_path / "graphs" / region / build_id)
        (tmp_path / "graphs" / region / "latest").write_text("b1\n")
    ran: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: ran.update(app=application))
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    monkeypatch.setenv("WORST_ROUTE_BASEMAP", "pmtiles")
    settings.cache_clear()
    base = ["serve", "--region", "oregon", "--region", "portland", "--backend", "python"]
    base += ["--geocoder", "none", "--store", str(tmp_path / "r.db")]
    served = runner.invoke(app, [*base, "--build-id", "b2"])  # the first region's
    assert served.exit_code == 0, served.output
    api = ran["app"].state.api
    assert api.settings.builds == (("oregon", "b2"), ("portland", "latest"))
    assert [(r, b.data.meta["build_id"]) for r, b in api.builds.items()] == [
        ("oregon", "b2"),
        ("portland", "b1"),
    ]
    assert served.stdout.splitlines()[-1] == (
        f"basemap: pmtiles {tmp_path / 'basemap' / 'oregon.pmtiles'}"  # the first region's
    )
    named = runner.invoke(app, [*base, "--basemap-name", "us"])
    assert named.exit_code == 0, named.output
    assert ran["app"].state.api.settings.basemap_name == "us"
    assert (
        named.stdout.splitlines()[-1] == f"basemap: pmtiles {tmp_path / 'basemap' / 'us.pmtiles'}"
    )
    twice = runner.invoke(app, [*base, "--region", "oregon"])
    assert twice.exit_code == 2 and "oregon given more than once" in twice.output
    unknown = runner.invoke(app, [*base, "--region", "atlantis"])
    assert unknown.exit_code == 2 and "unknown region 'atlantis'" in unknown.output
    monkeypatch.delenv("WORST_ROUTE_BASEMAP")
    settings.cache_clear()


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


def test_serve_takes_the_proxy_log_and_public_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    from worst_route.api.logs import JsonFormatter

    g = grid_graph(3, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    write_build(g, tmp_path / "graphs" / "portland" / "b1")
    (tmp_path / "graphs" / "portland" / "latest").write_text("b1\n")
    ran: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: ran.update(app=application, **kw))
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    settings.cache_clear()
    base = ["serve", "--region", "portland", "--backend", "python", "--geocoder", "none"]
    base += ["--store", str(tmp_path / "r.db")]
    plain = runner.invoke(app, base)
    assert plain.exit_code == 0, plain.output
    assert ran["app"].state.api.settings.trust_proxy is False
    # uvicorn's own proxy headers stay off: only --trust-proxy names the client
    assert (ran["proxy_headers"], ran["access_log"]) == (False, True)
    assert ran["log_config"]["loggers"]["worst_route.access"]["level"] == "WARNING"
    served = runner.invoke(app, [*base, "--trust-proxy", "--log-format", "json"])
    assert served.exit_code == 0, served.output
    assert served.stdout.splitlines()[-1] == "trusting the proxy's X-Forwarded-For and X-Request-ID"
    assert ran["app"].state.api.settings.trust_proxy is True
    assert (ran["proxy_headers"], ran["access_log"]) == (False, False)
    config = ran["log_config"]
    assert config["formatters"]["json"]["()"] is JsonFormatter
    assert config["loggers"]["worst_route"]["handlers"] == ["json"]
    assert config["handlers"]["json"]["stream"] == "ext://sys.stdout"
    assert runner.invoke(app, [*base, "--log-format", "yaml"]).exit_code == 2
    public = runner.invoke(app, [*base, "--public"])
    assert public.exit_code == 0, public.output
    assert public.stdout.splitlines()[-1] == (
        "public: a client runs 1 job at once with 3 waiting, budgets up to 60 s, "
        "10 place searches, 60 picks of a build, 240 drawing steps and 10 finished drawings "
        "a minute"
    )
    api = ran["app"].state.api
    assert api.settings.public is True and api.limits.budget_max_s == 60
    assert (api.jobs.settings.running_per_client, api.jobs.settings.queued_per_client) == (1, 3)
    assert api.quota is not None and api.quota.per_minute == 10
    monkeypatch.setenv("WORST_ROUTE_BASEMAP", "pmtiles")
    settings.cache_clear()
    vector = runner.invoke(app, base)
    assert vector.exit_code == 0, vector.output
    assert vector.stdout.splitlines()[-1] == (
        f"basemap: pmtiles {tmp_path / 'basemap' / 'portland.pmtiles'}"
    )
    assert ran["app"].state.api.settings.basemap == "pmtiles"
    monkeypatch.delenv("WORST_ROUTE_BASEMAP")
    settings.cache_clear()
    dist = tmp_path / "dist"
    dist.mkdir()
    unbuilt = runner.invoke(app, [*base, "--frontend", str(dist)])
    assert unbuilt.exit_code == 1
    assert unbuilt.output.strip().endswith("run `npm run build` under frontend/")
    (dist / "index.html").write_text("<!doctype html>")
    site = runner.invoke(app, [*base, "--frontend", str(dist)])
    assert site.exit_code == 0, site.output
    assert site.stdout.splitlines()[-1] == f"frontend: {dist} at /, the API under /api"
    assert [getattr(r, "path", None) for r in ran["app"].routes] == ["/api", ""]
    settings.cache_clear()
