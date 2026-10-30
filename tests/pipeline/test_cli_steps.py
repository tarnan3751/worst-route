import functools
import hashlib
import json
import pickle
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from worst_route.cli import app
from worst_route.config import REGIONS
from worst_route.pipeline import download, layout

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
runner = CliRunner()


def pipeline(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["pipeline", *args])
    return result.exit_code, result.output


def test_download_skips_a_current_extract(data_dir: Path) -> None:
    raw = data_dir / "raw"
    raw.mkdir(parents=True)
    (raw / "oregon-latest.osm.pbf").write_bytes(b"extract")
    md5 = hashlib.md5(b"extract").hexdigest()
    (raw / "oregon-latest.osm.pbf.md5").write_text(f"{md5}  oregon-latest.osm.pbf\n")
    code, output = pipeline("download", "--region", "portland")
    assert code == 0, output
    assert output.startswith("skipped: ") and "matches its md5" in output


def fake_fetch(published: bytes) -> Any:
    """A fetch that serves b"extract" and the md5 of `published` for it."""

    def fetch(url: str, dest: Path) -> None:
        md5 = hashlib.md5(published).hexdigest()
        dest.write_bytes(f"{md5}  x\n".encode() if url.endswith(".md5") else b"extract")

    return fetch


def test_download_through_the_cli(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = download.download_state
    monkeypatch.setattr(
        download, "download_state", functools.partial(real, fetch=fake_fetch(b"extract"))
    )
    code, output = pipeline("download", "--region", "portland")
    assert code == 0, output
    assert output.startswith("wrote ") and output.strip().endswith("md5 verified")
    assert (data_dir / "raw" / "oregon-latest.osm.pbf").read_bytes() == b"extract"


def test_download_md5_mismatch_exits_with_one_line(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = download.download_state
    monkeypatch.setattr(
        download, "download_state", functools.partial(real, fetch=fake_fetch(b"other"))
    )
    result = runner.invoke(app, ["pipeline", "download", "--region", "portland"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("download failed: oregon-latest.osm.pbf: md5 ")
    assert result.stderr.count("\n") == 1 and "Traceback" not in result.stderr
    assert not (data_dir / "raw" / "oregon-latest.osm.pbf").exists()


def test_download_http_error_exits_with_one_line(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def not_found(method: str, url: str, **kwargs: Any) -> Iterator[httpx.Response]:
        yield httpx.Response(404, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "stream", not_found)  # the real fetch, a fake server
    result = runner.invoke(app, ["pipeline", "download", "--region", "portland"])
    assert result.exit_code == 1
    assert result.stderr.startswith("download failed: Client error '404 Not Found'")
    assert result.stderr.count("\n") == 1 and "Traceback" not in result.stderr


def test_filter_from_a_source(data_dir: Path) -> None:
    code, output = pipeline(
        "filter", "--region", "portland", "--source", str(FIXTURES / "grid.osm")
    )
    assert code == 0, output
    roads, pois = (
        data_dir / "filtered" / "portland.roads.osm.pbf",
        data_dir / "filtered" / "portland.pois.parquet",
    )
    assert roads.exists() and pois.exists()
    assert "ways: read 12, kept 12, excluded 0, outside bbox 0, duplicate 0" in output
    assert "pois: 6 (brand 2, level_crossing 1, stop 1, toll_booth 1, traffic_signals 1)" in output
    assert pq.read_table(pois).num_rows == 6
    sidecar = json.loads((data_dir / "filtered" / "portland.roads.osm.pbf.inputs.json").read_text())
    assert sidecar == {
        "step": "filter",
        "inputs": [{"file": "grid.osm", "md5": layout.file_md5(FIXTURES / "grid.osm")}],
        "source_timestamp": None,  # the fixture's header has none; Geofabrik's extracts do
        "params": {"bbox": list(REGIONS["portland"].bbox or ()), "pipeline_version": "1"},
    }


def test_filter_skips_unless_forced_or_changed(data_dir: Path) -> None:
    grid = str(FIXTURES / "grid.osm")
    assert pipeline("filter", "--region", "portland", "--source", grid)[0] == 0
    roads = data_dir / "filtered" / "portland.roads.osm.pbf"
    written = roads.stat().st_mtime_ns
    code, output = pipeline("filter", "--region", "portland", "--source", grid)
    assert code == 0 and output.startswith("skipped: ")
    assert roads.stat().st_mtime_ns == written
    code, output = pipeline("filter", "--region", "portland", "--source", grid, "--force")
    assert code == 0 and output.startswith("wrote ")
    # Another input, or a missing output, runs the step again without --force.
    code, output = pipeline(
        "filter", "--region", "portland", "--source", str(FIXTURES / "culdesac.osm")
    )
    assert code == 0 and "kept 4" in output
    (data_dir / "filtered" / "portland.pois.parquet").unlink()
    code, output = pipeline(
        "filter", "--region", "portland", "--source", str(FIXTURES / "culdesac.osm")
    )
    assert code == 0 and output.startswith("wrote ")


def test_filter_clips_to_the_region(data_dir: Path, tmp_path: Path) -> None:
    # Copy bbox.osm far outside Portland: nothing survives the clip.
    code, output = pipeline(
        "filter", "--region", "portland", "--source", str(FIXTURES / "bbox.osm")
    )
    assert code == 0 and "kept 4" in output
    far = tmp_path / "far.osm"
    far.write_text((FIXTURES / "bbox.osm").read_text().replace('lon="-122.', 'lon="-100.'))
    code, output = pipeline("filter", "--region", "portland", "--source", str(far))
    assert (
        code == 0
        and "kept 0" in output
        and "outside bbox 4" in output
        and "pois: 0 (none)" in output
    )


def test_filter_needs_its_input(data_dir: Path) -> None:
    code, output = pipeline("filter", "--region", "portland")
    assert code == 1
    assert "missing input" in output and "pipeline download --region portland" in output


def test_build_after_filter(data_dir: Path) -> None:
    assert (
        pipeline("filter", "--region", "portland", "--source", str(FIXTURES / "grid.osm"))[0] == 0
    )
    roads = data_dir / "filtered" / "portland.roads.osm.pbf"
    build_id = layout.build_id_for(layout.file_md5(roads), REGIONS["portland"])
    code, output = pipeline("build", "--region", "portland")
    assert code == 0, output
    assert output.startswith(f"build {build_id}: wrote ")
    assert "ways: read 12, kept 12; segments 60, nodes 36, edges 110" in output
    assert "edges by one-way rule: oneway 5, oneway_reverse 5, two_way 100" in output
    stages = data_dir / "graphs" / "portland" / build_id / "stages"
    assert sorted(p.name for p in stages.iterdir()) == [
        "build.json",
        "build.pkl",
        "build.pkl.inputs.json",
    ]
    with (stages / "build.pkl").open("rb") as f:
        assert pickle.load(f).number_of_edges() == 110
    assert json.loads((stages / "build.json").read_text())["nodes"] == 36
    assert (data_dir / "graphs" / "portland" / "latest").read_text() == f"{build_id}\n"


def test_build_skips_unless_forced(data_dir: Path) -> None:
    oneway = str(FIXTURES / "oneway.osm")
    assert pipeline("build", "--region", "portland", "--source", oneway, "--build-id", "b1")[0] == 0
    pkl = data_dir / "graphs" / "portland" / "b1" / "stages" / "build.pkl"
    written = pkl.stat().st_mtime_ns
    code, output = pipeline("build", "--region", "portland", "--source", oneway, "--build-id", "b1")
    assert code == 0 and output.startswith("skipped: build b1 is up to date")
    assert pkl.stat().st_mtime_ns == written
    code, output = pipeline(
        "build", "--region", "portland", "--source", oneway, "--build-id", "b1", "--force"
    )
    assert code == 0 and output.startswith("build b1: wrote ")
    # The same id from another input is rebuilt.
    culdesac = str(FIXTURES / "culdesac.osm")
    code, output = pipeline(
        "build", "--region", "portland", "--source", culdesac, "--build-id", "b1"
    )
    assert code == 0 and "edges 12" in output


def test_build_records_the_latest_build(data_dir: Path) -> None:
    for build_id in ("b1", "b2", "b1"):
        args = ["--source", str(FIXTURES / "culdesac.osm"), "--build-id", build_id]
        assert pipeline("build", "--region", "portland", *args)[0] == 0
        assert (data_dir / "graphs" / "portland" / "latest").read_text() == f"{build_id}\n"


def test_build_from_an_unfiltered_source(data_dir: Path, tmp_path: Path) -> None:
    source = tmp_path / "exclusions.osm"
    shutil.copy(FIXTURES / "exclusions.osm", source)
    code, output = pipeline("build", "--region", "portland", "--source", str(source))
    assert code == 0, output
    assert "ways: read 16, kept 5; segments 8, nodes 9, edges 16" in output


def test_build_needs_its_input(data_dir: Path) -> None:
    code, output = pipeline("build", "--region", "portland")
    assert code == 1
    assert "missing input" in output and "pipeline filter --region portland" in output


def test_importing_the_cli_does_not_load_the_geo_stack() -> None:
    heavy = ["httpx", "networkx", "numpy", "osmium", "pyarrow", "pyproj", "shapely"]
    probe = f"import sys, worst_route.cli; print([m for m in {heavy!r} if m in sys.modules])"
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_cleanup_after_build(data_dir: Path) -> None:
    source = str(FIXTURES / "divided.osm")
    assert pipeline("build", "--region", "portland", "--source", source, "--build-id", "b1")[0] == 0
    code, output = pipeline("cleanup", "--region", "portland")  # the latest build: b1
    assert code == 0, output
    stages = data_dir / "graphs" / "portland" / "b1" / "stages"
    assert output.splitlines() == [
        f"cleanup b1: wrote {stages / 'cleanup.pkl'} "
        f"({(stages / 'cleanup.pkl').stat().st_size / 1e6:.1f} MB)",
        "simplify: dropped 0 self-loops, removed 1 nodes",
        "stage A: 1 clusters from 4 nodes",
        "twins: 7 of 7 candidate edges paired; motorway and trunk coverage n/a",
        "stage B: 0 super-junctions (interchange 0, roundabout 0), 0 internal edges, "
        "5 twin pairs carried",
        "connectivity: kept 15 of 15 nodes; dropped 0 nodes and 0.0 km in 0 components",
        "nodes 15, edges 25",
    ]
    assert {p.name for p in stages.iterdir()} >= {
        "cleanup.pkl",
        "cleanup.json",
        "cleanup.pkl.inputs.json",
    }
    with (stages / "cleanup.pkl").open("rb") as f:
        assert pickle.load(f).number_of_nodes() == 15
    assert json.loads((stages / "cleanup.json").read_text())["stage_a"]["clusters"] == 1
    sidecar = json.loads((stages / "cleanup.pkl.inputs.json").read_text())
    assert sidecar["inputs"] == [
        {"file": "build.pkl", "md5": layout.file_md5(stages / "build.pkl")}
    ]
    assert sidecar["params"]["allow_fragmented"] is False
    assert sidecar["params"]["config"]["STAGE_B_JOIN_M"] == 500.0


def test_cleanup_skips_unless_forced(data_dir: Path) -> None:
    source = str(FIXTURES / "diamond.osm")
    assert pipeline("build", "--region", "portland", "--source", source, "--build-id", "b2")[0] == 0
    assert pipeline("cleanup", "--region", "portland", "--build-id", "b2")[0] == 0
    pkl = data_dir / "graphs" / "portland" / "b2" / "stages" / "cleanup.pkl"
    written = pkl.stat().st_mtime_ns
    code, output = pipeline("cleanup", "--region", "portland", "--build-id", "b2")
    assert code == 0 and output.startswith("skipped: cleanup of build b2 is up to date")
    assert pkl.stat().st_mtime_ns == written
    code, output = pipeline("cleanup", "--region", "portland", "--build-id", "b2", "--force")
    assert code == 0 and output.startswith("cleanup b2: wrote ")
    assert (
        "stage B: 1 super-junctions (interchange 1, roundabout 0), 8 internal edges, "
        "2 twin pairs carried" in output
    )
    assert "motorway and trunk coverage 1.000" in output


def test_cleanup_of_a_fragmented_graph(data_dir: Path) -> None:
    source = str(FIXTURES / "oneway.osm")
    assert pipeline("build", "--region", "portland", "--source", source, "--build-id", "b3")[0] == 0
    stages = data_dir / "graphs" / "portland" / "b3" / "stages"
    result = runner.invoke(app, ["pipeline", "cleanup", "--region", "portland"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith(
        "cleanup failed: the largest strongly connected component keeps 3 of 13 nodes (23.08%)"
    )
    assert result.stderr.count("\n") == 1 and "Traceback" not in result.stderr
    assert not (stages / "cleanup.pkl").exists() and not (stages / "cleanup.json").exists()
    code, output = pipeline("cleanup", "--region", "portland", "--allow-fragmented")
    assert code == 0, output
    assert "connectivity: kept 3 of 13 nodes; dropped 10 nodes" in output
    assert (stages / "cleanup.pkl").exists()


def test_cleanup_needs_a_build(data_dir: Path) -> None:
    code, output = pipeline("cleanup", "--region", "portland")
    assert code == 1
    assert "missing input" in output and "pipeline build --region portland" in output
    code, output = pipeline("cleanup", "--region", "portland", "--build-id", "nope")
    assert code == 1 and "nope" in output and "build.pkl" in output
