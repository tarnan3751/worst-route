import dataclasses
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
import numpy as np
import osmium
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from tests.pipeline.invariants import check_build
from tests.pipeline.test_carriageways import mapped_twice
from tests.pipeline.test_cleanup import cut_interchange
from worst_route.cli import app
from worst_route.config import REGIONS, Region
from worst_route.graph.store import read_build
from worst_route.pipeline import PIPELINE_VERSION, download, layout
from worst_route.pipeline.arrays import cleaned
from worst_route.pipeline.arrays.build import load_stage
from worst_route.pipeline.cleanup import run_arrays_cleanup

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
        "inputs": [
            {
                "file": "grid.osm",
                "md5": layout.file_md5(FIXTURES / "grid.osm"),
                "source_timestamp": None,  # the fixture's header has none; Geofabrik's do
            }
        ],
        "params": {
            "bbox": list(REGIONS["portland"].bbox or ()),
            "pipeline_version": PIPELINE_VERSION,
        },
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


BORDER = Region(name="border", states=("south", "north"), crs_epsg=32610)


def raw_extract(state: str, fixture: str, timestamp: str | None = None) -> Path:
    """The fixture written as `state`'s Geofabrik extract, with that data date in its header."""
    path = layout.raw_pbf(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = osmium.io.Header()
    if timestamp is not None:
        header.set("osmosis_replication_timestamp", timestamp)
    with osmium.SimpleWriter(path, header=header) as writer:
        for obj in osmium.FileProcessor(FIXTURES / f"{fixture}.osm"):
            writer.add(obj)
    return path


def test_download_fetches_every_state_of_a_region(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(REGIONS, "border", BORDER)
    real = download.download_state
    monkeypatch.setattr(
        download, "download_state", functools.partial(real, fetch=fake_fetch(b"extract"))
    )
    code, output = pipeline("download", "--region", "border")
    assert code == 0, output
    raw = data_dir / "raw"
    assert [line.split(" (")[0] for line in output.splitlines()] == [
        f"wrote {raw / 'south-latest.osm.pbf'}",
        f"wrote {raw / 'north-latest.osm.pbf'}",
    ]
    code, output = pipeline("download", "--region", "border")
    assert code == 0 and [line.split(" matches")[0] for line in output.splitlines()] == [
        f"skipped: {raw / 'south-latest.osm.pbf'}",
        f"skipped: {raw / 'north-latest.osm.pbf'}",
    ]


def test_filter_merges_the_extracts_of_a_region_of_several_states(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(REGIONS, "border", BORDER)
    south = raw_extract("south", "border_south", "2026-10-29T21:21:26Z")
    north = raw_extract("north", "border_north", "2026-10-28T21:21:02Z")
    result = runner.invoke(app, ["pipeline", "filter", "--region", "border"])
    assert result.exit_code == 0, result.output
    assert "ways: read 4, kept 3, excluded 0, outside bbox 0, duplicate 1" in result.stdout
    assert "pois: 2 (brand 1, traffic_signals 1)" in result.stdout
    assert result.stderr == (
        "warning: the extracts hold data of different dates: "
        "2026-10-28T21:21:02Z, 2026-10-29T21:21:26Z\n"
    )
    sidecar = json.loads(layout.sidecar(layout.roads_pbf("border")).read_text())
    assert sidecar["inputs"] == [
        {
            "file": "south-latest.osm.pbf",
            "md5": layout.file_md5(south),
            "source_timestamp": "2026-10-29T21:21:26Z",
        },
        {
            "file": "north-latest.osm.pbf",
            "md5": layout.file_md5(north),
            "source_timestamp": "2026-10-28T21:21:02Z",
        },
    ]
    code, output = pipeline("build", "--region", "border")
    assert code == 0 and "segments 3, nodes 4, edges 6" in output
    north.unlink()
    code, output = pipeline("filter", "--region", "border", "--force")
    assert code == 1
    assert f"missing input {north}: run `worst-route pipeline download --region border`" in output


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
    assert sorted(p.name for p in stages.iterdir()) == ["build", "build.inputs.json", "build.json"]
    assert load_stage(stages.parent).edges == 110
    assert json.loads((stages / "build.json").read_text())["nodes"] == 36
    assert (data_dir / "graphs" / "portland" / "latest").read_text() == f"{build_id}\n"


def test_build_skips_unless_forced(data_dir: Path) -> None:
    oneway = str(FIXTURES / "oneway.osm")
    assert pipeline("build", "--region", "portland", "--source", oneway, "--build-id", "b1")[0] == 0
    manifest = data_dir / "graphs" / "portland" / "b1" / "stages" / "build" / "manifest.json"
    written = manifest.stat().st_mtime_ns
    code, output = pipeline("build", "--region", "portland", "--source", oneway, "--build-id", "b1")
    assert code == 0 and output.startswith("skipped: build b1 is up to date")
    assert manifest.stat().st_mtime_ns == written
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
    heavy = ["httpx", "networkx", "numba", "numpy", "osmium", "pyarrow", "pyproj", "shapely"]
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
    size = sum(f.stat().st_size for f in (stages / "cleanup").iterdir())
    assert output.splitlines() == [
        f"cleanup b1: wrote {stages / 'cleanup'} ({size / 1e6:.1f} MB)",
        "simplify: dropped 0 self-loops, removed 1 nodes",
        "stage A: 1 clusters from 4 nodes",
        "twins: 7 of 7 candidate edges paired; motorway and trunk coverage n/a",
        "stage B: 0 super-junctions (interchange 0, roundabout 0), 0 internal edges, "
        "5 twin pairs carried, 0 joins along a carriageway",
        "connectivity: kept 15 of 15 nodes; dropped 0 nodes and 0.0 km in 0 components",
        "nodes 15, edges 25",
    ]
    assert {p.name for p in stages.iterdir()} >= {"cleanup", "cleanup.json", "cleanup.inputs.json"}
    assert len(cleaned.load_stage(stages.parent).node_x) == 15
    assert json.loads((stages / "cleanup.json").read_text())["stage_a"]["clusters"] == 1
    sidecar = json.loads((stages / "cleanup.inputs.json").read_text())
    manifest = stages / "build" / "manifest.json"
    assert sidecar["inputs"] == [{"file": "build/manifest.json", "md5": layout.file_md5(manifest)}]
    assert sidecar["params"]["allow_fragmented"] is False
    assert sidecar["params"]["config"]["STAGE_B_JOIN_M"] == 500.0


def test_cleanup_counts_the_super_junctions_dropped_with_no_allowed_turn(
    data_dir: Path, tmp_path: Path
) -> None:
    source = str(cut_interchange(tmp_path))
    assert pipeline("build", "--region", "portland", "--source", source, "--build-id", "b1")[0] == 0
    code, output = pipeline("cleanup", "--region", "portland", "--allow-fragmented")
    assert code == 0, output
    lines = output.splitlines()
    assert lines[4].startswith("stage B: 1 super-junctions (interchange 1, roundabout 0)")
    assert lines[5].startswith("connectivity: kept 2 of ")
    assert lines[5].endswith(" components, 1 super-junctions dropped with no allowed turn")


def test_cleanup_counts_the_pairs_of_one_road_mapped_twice(data_dir: Path, tmp_path: Path) -> None:
    source = str(mapped_twice(tmp_path))
    assert pipeline("build", "--region", "portland", "--source", source, "--build-id", "b1")[0] == 0
    code, output = pipeline("cleanup", "--region", "portland")
    assert code == 0, output
    assert output.splitlines()[3] == (
        "twins: 0 of 2 candidate edges paired; motorway and trunk coverage n/a, "
        "1 pairs of one road mapped twice"
    )


def test_cleanup_skips_unless_forced(data_dir: Path) -> None:
    source = str(FIXTURES / "diamond.osm")
    assert pipeline("build", "--region", "portland", "--source", source, "--build-id", "b2")[0] == 0
    assert pipeline("cleanup", "--region", "portland", "--build-id", "b2")[0] == 0
    manifest = data_dir / "graphs" / "portland" / "b2" / "stages" / "cleanup" / "manifest.json"
    written = manifest.stat().st_mtime_ns
    code, output = pipeline("cleanup", "--region", "portland", "--build-id", "b2")
    assert code == 0 and output.startswith("skipped: cleanup of build b2 is up to date")
    assert manifest.stat().st_mtime_ns == written
    code, output = pipeline("cleanup", "--region", "portland", "--build-id", "b2", "--force")
    assert code == 0 and output.startswith("cleanup b2: wrote ")
    assert (
        "stage B: 1 super-junctions (interchange 1, roundabout 0), 8 internal edges, "
        "2 twin pairs carried, 0 joins along a carriageway" in output
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
    assert not (stages / "cleanup").exists() and not (stages / "cleanup.json").exists()
    code, output = pipeline("cleanup", "--region", "portland", "--allow-fragmented")
    assert code == 0, output
    assert "connectivity: kept 3 of 13 nodes; dropped 10 nodes" in output
    assert (stages / "cleanup" / "manifest.json").exists()


def test_cleanup_needs_a_build(data_dir: Path) -> None:
    code, output = pipeline("cleanup", "--region", "portland")
    assert code == 1
    assert "missing input" in output and "pipeline build --region portland" in output
    code, output = pipeline("cleanup", "--region", "portland", "--build-id", "nope")
    assert code == 1 and "nope" in output and "build/manifest.json" in output


def test_the_arrays_engine_through_the_cli(data_dir: Path) -> None:
    """`build` and `cleanup` with `--engine` write and read their own engine's stages, and
    `enrich`, `tiers` and `save` follow the engine cleanup ran: the arrays engine's stages are
    directories, no pickle among them, and both engines save one build. Switching a build's
    engine in place reruns the stages after cleanup, each engine's own, to the same build."""
    diamond = str(FIXTURES / "diamond.osm")
    assert pipeline("filter", "--region", "portland", "--source", diamond)[0] == 0
    versions = {}
    for engine in ("networkx", "arrays"):
        flags = ["--region", "portland", "--build-id", engine]
        code, output = pipeline("build", *flags, "--engine", engine)
        assert code == 0 and output.splitlines()[0].endswith(f", {engine})"), output
        assert pipeline("cleanup", *flags, "--engine", engine)[0] == 0
        assert pipeline("enrich", *flags)[0] == 0
        assert pipeline("tiers", *flags, "--allow-fragmented")[0] == 0
        code, output = pipeline("save", *flags)
        assert code == 0, output
        versions[engine] = json.loads(
            (layout.build_dir("portland", engine) / "meta.json").read_text()
        )
    assert versions["arrays"]["graph_version"] == versions["networkx"]["graph_version"]
    assert versions["arrays"]["params"] == versions["networkx"]["params"]
    names = {p.name for p in (layout.build_dir("portland", "networkx") / "stages").iterdir()}
    assert {"build.pkl", "cleanup.pkl", "enrich.pkl"} <= names
    assert not names & {"build", "cleanup", "enrich"}

    stages = layout.build_dir("portland", "arrays") / "stages"
    names = {p.name for p in stages.iterdir()}
    assert {"build", "cleanup", "enrich", "build.inputs.json", "cleanup.inputs.json"} <= names
    assert not {name for name in names if name.endswith(".pkl")}
    assert json.loads((stages / "build.inputs.json").read_text())["engine"] == "arrays"
    sidecar = json.loads((stages / "cleanup.inputs.json").read_text())
    manifest = stages / "build" / "manifest.json"
    assert sidecar["engine"] == "arrays"
    assert sidecar["inputs"] == [{"file": "build/manifest.json", "md5": layout.file_md5(manifest)}]
    flags = ["--region", "portland", "--build-id", "arrays"]
    code, output = pipeline("cleanup", *flags, "--engine", "arrays")
    assert code == 0 and output.startswith("skipped: ")
    code, output = pipeline("cleanup", *flags, "--engine", "networkx")  # its input is not there
    assert code == 1 and "build.pkl" in output and "--engine networkx" in output

    # The same build switched to the networkx engine in place: its stages after cleanup rerun.
    assert pipeline("build", *flags, "--engine", "networkx")[0] == 0
    assert pipeline("cleanup", *flags, "--engine", "networkx")[0] == 0
    assert not (stages / "cleanup").exists() and (stages / "cleanup.pkl").exists()
    code, output = pipeline("tiers", *flags)  # networkx's enrich stage is not there yet
    assert code == 1 and "enrich.pkl" in output and "pipeline enrich --region" in output
    assert pipeline("enrich", *flags)[0] == 0
    assert not (stages / "enrich").exists() and (stages / "enrich.pkl").exists()
    assert pipeline("tiers", *flags, "--allow-fragmented")[0] == 0
    assert pipeline("save", *flags, "--force")[0] == 0
    meta = json.loads((stages.parent / "meta.json").read_text())
    assert meta["graph_version"] == versions["arrays"]["graph_version"]
    manifest.write_text(manifest.read_text() + "\n")  # the arrays build stage, not read now
    assert pipeline("enrich", *flags)[1].startswith("skipped: ")
    (stages / "build.pkl").write_bytes((stages / "build.pkl").read_bytes() + b"\n")
    code, output = pipeline("enrich", *flags, "--force")
    assert code == 1 and "stage cleanup is stale" in output


@pytest.mark.parametrize("engine", ["arrays", "networkx"])
def test_a_cleanup_changing_only_what_enrich_never_reads(data_dir: Path, engine: str) -> None:
    """The cleanup stage written again with its twins gone, which enrich never reads: enrich runs
    again, tiers too, and save refuses to keep the build it wrote from the old stage, on both
    engines (the arrays engine's enrich stage records the cleanup stage it lies on, and tiers and
    save record it)."""
    stages = cleaned_build(str(FIXTURES / "divided.osm"), "b1", "--engine", engine)
    for step in ("enrich", "tiers", "save"):
        assert pipeline(step, "--region", "portland")[0] == 0
    if engine == "arrays":
        graph, report = run_arrays_cleanup(load_stage(stages.parent))
        twins = {
            "twin_indptr": np.zeros_like(graph.twin_indptr),
            "twin_edges": graph.twin_edges[:0],
        }
        cleaned.save_stage(dataclasses.replace(graph, **twins), report, stages.parent)
    else:
        with (stages / "cleanup.pkl").open("rb") as f:
            networkx_graph = pickle.load(f)
        networkx_graph.graph["twins"] = {}
        with (stages / "cleanup.pkl").open("wb") as f:
            pickle.dump(networkx_graph, f, protocol=pickle.HIGHEST_PROTOCOL)
    assert pipeline("enrich", "--region", "portland")[1].startswith("enrich b1: wrote ")
    assert pipeline("tiers", "--region", "portland")[1].startswith("tiers b1: wrote ")
    result = runner.invoke(app, ["pipeline", "save", "--region", "portland"])
    assert result.exit_code == 1 and "already written" in result.stderr
    assert pipeline("save", "--region", "portland", "--force")[0] == 0
    meta = json.loads((stages.parent / "meta.json").read_text())
    assert meta["counts"]["twin_pairs"] == 0


def test_sidecars_from_before_the_engine_are_the_networkx_engines(data_dir: Path) -> None:
    """A build and a cleanup whose sidecars do not record an engine, as the code before the
    arrays engine wrote them, are up to date for the networkx engine, not for arrays."""
    stages = cleaned_build(str(FIXTURES / "divided.osm"), "b1", "--engine", "networkx")
    for sidecar in ("build.pkl.inputs.json", "cleanup.pkl.inputs.json"):
        record = json.loads((stages / sidecar).read_text())
        del record["engine"]
        (stages / sidecar).write_text(json.dumps(record, indent=2) + "\n")
    flags = ["--region", "portland", "--build-id", "b1"]
    for step in ("build", "cleanup"):
        code, output = pipeline(step, *flags, "--engine", "networkx")
        assert code == 0 and output.startswith("skipped: "), output
    code, output = pipeline("cleanup", *flags, "--engine", "arrays")
    assert code == 1 and "build/manifest.json" in output  # its own build stage is not there


def test_an_arrays_stage_without_its_manifest_is_rebuilt(data_dir: Path) -> None:
    flags = ["--region", "portland", "--source", str(FIXTURES / "grid.osm"), "--build-id", "b1"]
    assert pipeline("build", *flags, "--engine", "arrays")[0] == 0
    manifest = layout.stage_file(layout.build_dir("portland", "b1"), "build/manifest.json")
    manifest.unlink()  # an interrupted write
    code, output = pipeline("build", *flags, "--engine", "arrays")
    assert code == 0 and output.startswith("build b1: wrote ") and manifest.exists()


def cleaned_build(source: str, build_id: str = "b1", *engine: str) -> Path:
    """Filter, build and clean a fixture under the test's data directory (with `--engine` and
    an engine, if given); the stages."""
    assert pipeline("filter", "--region", "portland", "--source", source)[0] == 0
    assert pipeline("build", "--region", "portland", "--build-id", build_id, *engine)[0] == 0
    assert pipeline("cleanup", "--region", "portland", "--build-id", build_id, *engine)[0] == 0
    return layout.build_dir("portland", build_id) / "stages"


def test_enrich_after_cleanup(data_dir: Path) -> None:
    stages = cleaned_build(str(FIXTURES / "grid.osm"))
    code, output = pipeline("enrich", "--region", "portland")  # the latest build: b1
    assert code == 0, output
    size = sum(f.stat().st_size for f in (stages / "enrich").iterdir())
    assert output.splitlines() == [
        f"enrich b1: wrote {stages / 'enrich'} ({size / 1e6:.1f} MB)",
        "speeds: 10 edges from maxspeed, 100 at their class default",
        "identities: 12 on 110 edges; units: 60 (50 two-way pairs)",
        "flags: bridge 0, tunnel 0, toll 0, roundabout 0, link 0",
        "toll_booth: 1 found, 1 on edges, 0 at junctions, 0 off the graph; on 2 edges",
        "level_crossing: 1 found, 1 on edges, 0 at junctions, 0 off the graph; on 2 edges",
        "stop: 1 found, 1 at junctions (a delay), 0 shape points (none), 0 off the graph",
        "traffic_signals: 1 found, 1 at junctions (a delay), 0 shape points (none), "
        "0 off the graph",
        "nodes: 2 with a delay, 36 of 36 pass-through",
        "brands: 2 POIs, 2 snapped, 0 skipped at dead ends, 0 too far; 0 of 2 brands kept, 0 "
        "brand-node rows on 0 nodes",
    ]
    assert json.loads((stages / "enrich.json").read_text())["units"] == 60
    sidecar = json.loads((stages / "enrich.inputs.json").read_text())
    files = [i["file"] for i in sidecar["inputs"]]
    assert files == ["cleanup/manifest.json", "portland.pois.parquet"]
    assert sidecar["params"]["config"]["BEARING_SAMPLE_M"] == 20.0
    assert sidecar["params"]["config"]["BRAND_SNAP_M"] == 100.0


def test_enrich_skips_unless_forced_or_changed(data_dir: Path) -> None:
    stages = cleaned_build(str(FIXTURES / "divided.osm"))
    assert pipeline("enrich", "--region", "portland")[0] == 0
    written = (stages / "enrich" / "manifest.json").stat().st_mtime_ns
    code, output = pipeline("enrich", "--region", "portland", "--build-id", "b1")
    assert code == 0 and output.startswith("skipped: enrich of build b1 is up to date")
    assert (stages / "enrich" / "manifest.json").stat().st_mtime_ns == written
    code, output = pipeline("enrich", "--region", "portland", "--force")
    assert code == 0 and output.startswith("enrich b1: wrote ")
    # Other POIs are another input: enrich runs again.
    grid = str(FIXTURES / "grid.osm")
    assert pipeline("filter", "--region", "portland", "--source", grid)[0] == 0
    code, output = pipeline("enrich", "--region", "portland")
    assert code == 0 and output.startswith("enrich b1: wrote ")


def test_enrich_refuses_a_stale_cleanup(data_dir: Path) -> None:
    stages = cleaned_build(str(FIXTURES / "divided.osm"))
    # The build is made again, from another file under the same id; cleanup is not rerun.
    source = str(FIXTURES / "culdesac.osm")
    assert pipeline("build", "--region", "portland", "--source", source, "--build-id", "b1")[0] == 0
    result = runner.invoke(app, ["pipeline", "enrich", "--region", "portland"])
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr == (
        "stage cleanup is stale: rerun `worst-route pipeline cleanup --region portland`\n"
    )
    assert not (stages / "enrich").exists()
    assert pipeline("cleanup", "--region", "portland")[0] == 0
    assert pipeline("enrich", "--region", "portland")[0] == 0


def test_enrich_needs_its_inputs(data_dir: Path) -> None:
    code, output = pipeline("enrich", "--region", "portland")
    assert code == 1 and "pipeline build --region portland" in output
    source = str(FIXTURES / "culdesac.osm")
    assert pipeline("build", "--region", "portland", "--source", source, "--build-id", "b1")[0] == 0
    code, output = pipeline("enrich", "--region", "portland")
    assert code == 1 and "cleanup/manifest.json" in output and "pipeline cleanup --region" in output
    assert pipeline("cleanup", "--region", "portland")[0] == 0
    code, output = pipeline("enrich", "--region", "portland")
    assert code == 1 and "pois.parquet" in output and "pipeline filter --region" in output


def test_tiers_after_enrich(data_dir: Path) -> None:
    stages = cleaned_build(str(FIXTURES / "divided.osm"))
    assert pipeline("enrich", "--region", "portland")[0] == 0
    code, output = pipeline("tiers", "--region", "portland")
    assert code == 0, output
    tiers_json = stages / "tiers.json"
    assert output.splitlines() == [
        f"tiers b1: wrote {tiers_json} ({tiers_json.stat().st_size / 1e6:.1f} MB)",
        "T0: kept 0 of 0 nodes (100.00%) and 0 of 0 edges; 0 components",
        "T1: kept 8 of 8 nodes (100.00%) and 11 of 11 edges; 1 components",
        "T2: kept 8 of 8 nodes (100.00%) and 11 of 11 edges; 1 components",
        "T3: kept 8 of 8 nodes (100.00%) and 11 of 11 edges; 1 components",
        "T4: kept 15 of 15 nodes (100.00%) and 25 of 25 edges; 1 components",
    ]
    sidecar = json.loads((stages / "tiers.json.inputs.json").read_text())
    assert sidecar["inputs"][0]["file"] == "enrich/manifest.json"
    assert sidecar["params"]["allow_fragmented"] is False
    code, output = pipeline("tiers", "--region", "portland")
    assert code == 0 and output.startswith("skipped: tiers of build b1 are up to date")
    code, output = pipeline("tiers", "--region", "portland", "--force")
    assert code == 0 and output.startswith("tiers b1: wrote ")


def test_tiers_of_a_fragmented_build(data_dir: Path) -> None:
    stages = cleaned_build(str(FIXTURES / "diamond.osm"))
    assert pipeline("enrich", "--region", "portland")[0] == 0
    result = runner.invoke(app, ["pipeline", "tiers", "--region", "portland"])
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr.startswith("tiers failed: T0 keeps 1 of 5 nodes (20.00%); ")
    assert result.stderr.count("\n") == 1 and "Traceback" not in result.stderr
    assert not (stages / "tiers.json").exists()
    code, output = pipeline("tiers", "--region", "portland", "--allow-fragmented")
    assert code == 0 and "T0: kept 1 of 5 nodes (20.00%)" in output
    assert (stages / "tiers.json").exists()


def test_tiers_refuse_a_stale_enrich(data_dir: Path) -> None:
    cleaned_build(str(FIXTURES / "divided.osm"))
    assert pipeline("enrich", "--region", "portland")[0] == 0
    # Cleanup made again from another build under the same id; enrich is not rerun.
    source = str(FIXTURES / "culdesac.osm")
    assert pipeline("build", "--region", "portland", "--source", source, "--build-id", "b1")[0] == 0
    assert pipeline("cleanup", "--region", "portland")[0] == 0
    result = runner.invoke(app, ["pipeline", "tiers", "--region", "portland"])
    assert result.exit_code == 1
    assert result.stderr.startswith("stage enrich is stale: rerun `worst-route pipeline enrich")


def test_tiers_need_enrich(data_dir: Path) -> None:
    cleaned_build(str(FIXTURES / "culdesac.osm"))
    code, output = pipeline("tiers", "--region", "portland")
    assert code == 1 and "enrich/manifest.json" in output and "pipeline enrich --region" in output


def saved_build(source: str, *, allow_fragmented: bool = False) -> Path:
    """Run every step up to save on a fixture; the build's directory (build id b1)."""
    stages = cleaned_build(source)
    assert pipeline("enrich", "--region", "portland")[0] == 0
    fragmented = ["--allow-fragmented"] if allow_fragmented else []
    assert pipeline("tiers", "--region", "portland", *fragmented)[0] == 0
    code, output = pipeline("save", "--region", "portland")
    assert code == 0, output
    return stages.parent


def test_save_after_tiers(data_dir: Path) -> None:
    directory = saved_build(str(FIXTURES / "grid.osm"), allow_fragmented=True)
    meta = json.loads((directory / "meta.json").read_text())
    assert sorted(p.name for p in directory.iterdir()) == [
        "arrays",
        "geometry",
        "meta.json",
        "stages",
        "strings.json",
    ]
    assert meta["region"] == "portland" and meta["build_id"] == "b1"
    assert meta["format"] == 4 and len(meta["geometry_version"]) == 64
    assert meta["counts"] == {
        "nodes": 36,
        "edges": 110,
        "twin_pairs": 0,
        "transitions": 0,
        "superjunctions": 0,
    }
    assert meta["sources"] == [
        {
            "file": "grid.osm",
            "md5": layout.file_md5(FIXTURES / "grid.osm"),
            "source_timestamp": None,
        }
    ]
    params = meta["params"]
    assert params["bbox"] == list(REGIONS["portland"].bbox or ())
    assert params["cleanup"]["allow_fragmented"] is False
    assert params["cleanup"]["config"]["STAGE_A_MAX_LENGTH_M"] == 60.0
    assert params["enrich"]["config"]["NODE_DELAY_S"] == {"stop": 10.0, "traffic_signals": 30.0}
    assert params["tiers"]["config"]["TIER_MIN_RETAINED"] == 0.95
    assert params["save"] == {"config": {"GEOMETRY_SIMPLIFY_M": 2.0}}
    assert "pipeline_version" not in params["enrich"]
    saved = json.loads((directory / "stages" / "save.json").read_text())
    assert saved["graph_version"] == meta["graph_version"]
    check_build(read_build(directory))


def test_save_output(data_dir: Path) -> None:
    cleaned_build(str(FIXTURES / "diamond.osm"))
    assert pipeline("enrich", "--region", "portland")[0] == 0
    assert pipeline("tiers", "--region", "portland", "--allow-fragmented")[0] == 0
    code, output = pipeline("save", "--region", "portland")
    assert code == 0, output
    directory = data_dir / "graphs" / "portland" / "b1"
    version = json.loads((directory / "meta.json").read_text())["graph_version"]
    lines = output.splitlines()
    assert lines[0].startswith(f"save b1: wrote {directory} (")
    assert lines[1:] == [
        f"graph_version {version}",
        "nodes 7, edges 12; 2 twin pairs; 16 transitions at 1 super-junctions",
    ]


def test_save_skips_refuses_and_forces(data_dir: Path) -> None:
    directory = saved_build(str(FIXTURES / "divided.osm"))
    meta = directory / "meta.json"
    written = json.loads(meta.read_text())
    code, output = pipeline("save", "--region", "portland")
    assert (
        code == 0 and output == f"skipped: build b1 is written at {directory} (--force to redo)\n"
    )
    # Other inputs under the same build id (here a tiers stage with another timing): the
    # written build stays, unless --force.
    tiers_json = directory / "stages" / "tiers.json"
    stage = json.loads(tiers_json.read_text())
    tiers_json.write_text(json.dumps({**stage, "seconds": stage["seconds"] + 1}))
    result = runner.invoke(app, ["pipeline", "save", "--region", "portland"])
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr == (
        f"save failed: build b1 is already written at {directory} from other inputs; a build "
        "is never overwritten (--force to replace it)\n"
    )
    assert json.loads(meta.read_text()) == written
    code, output = pipeline("save", "--region", "portland", "--force")
    assert code == 0 and output.startswith("save b1: wrote ")
    rewritten = json.loads(meta.read_text())
    assert rewritten["graph_version"] == written["graph_version"]  # same graph, same version


def test_save_refuses_stale_tiers(data_dir: Path) -> None:
    saved_build(str(FIXTURES / "divided.osm"))
    # Another extract under the same build id: build, cleanup and enrich run again, tiers not.
    assert (
        pipeline("filter", "--region", "portland", "--source", str(FIXTURES / "grid.osm"))[0] == 0
    )
    for step in ("build", "cleanup", "enrich"):
        assert pipeline(step, "--region", "portland", "--build-id", "b1")[0] == 0
    for args in ([], ["--force"]):
        result = runner.invoke(app, ["pipeline", "save", "--region", "portland", *args])
        assert result.exit_code == 1 and result.stdout == ""
        assert result.stderr == (
            "stage tiers is stale: rerun `worst-route pipeline tiers --region portland`\n"
        )


def test_save_records_the_parameters_it_copies(data_dir: Path) -> None:
    directory = saved_build(str(FIXTURES / "divided.osm"))
    written = json.loads((directory / "meta.json").read_text())
    # Cleanup again with another parameter: its stage is the same bytes, so enrich and tiers
    # skip, but the build's params would change.
    code, output = pipeline("cleanup", "--region", "portland", "--allow-fragmented")
    assert code == 0 and output.startswith("cleanup b1: wrote ")
    assert pipeline("enrich", "--region", "portland")[1].startswith("skipped: ")
    assert pipeline("tiers", "--region", "portland")[1].startswith("skipped: ")
    result = runner.invoke(app, ["pipeline", "save", "--region", "portland"])
    assert result.exit_code == 1 and "already written" in result.stderr
    assert pipeline("save", "--region", "portland", "--force")[0] == 0
    rewritten = json.loads((directory / "meta.json").read_text())
    assert rewritten["params"]["cleanup"]["allow_fragmented"] is True
    assert rewritten["graph_version"] != written["graph_version"]  # params enter the version


def test_save_refuses_sources_of_another_extract(data_dir: Path) -> None:
    saved_build(str(FIXTURES / "divided.osm"))
    # The region is filtered again from another file; the build still holds the first one.
    assert (
        pipeline("filter", "--region", "portland", "--source", str(FIXTURES / "grid.osm"))[0] == 0
    )
    result = runner.invoke(app, ["pipeline", "save", "--region", "portland", "--force"])
    assert result.exit_code == 1
    assert result.stderr == (
        "save failed: the filtered extract changed since this build was built; rebuild\n"
    )


def test_save_refuses_a_filter_record_without_a_date_per_extract(data_dir: Path) -> None:
    saved_build(str(FIXTURES / "divided.osm"))
    # The record as a filter before M5.3 wrote it: one data date, not one per extract.
    sidecar = layout.sidecar(layout.roads_pbf("portland"))
    record = json.loads(sidecar.read_text())
    for entry in record["inputs"]:
        del entry["source_timestamp"]
    sidecar.write_text(json.dumps({**record, "source_timestamp": None}))
    result = runner.invoke(app, ["pipeline", "save", "--region", "portland", "--force"])
    assert result.exit_code == 1
    assert result.stderr == (
        "save failed: the filter's record gives no data date per extract; rerun `worst-route "
        "pipeline filter --region portland` (its output stays the same)\n"
    )


def test_save_needs_the_sidecars_it_reads(data_dir: Path) -> None:
    directory = saved_build(str(FIXTURES / "divided.osm"))
    sidecar = directory / "stages" / "cleanup.inputs.json"
    sidecar.unlink()
    result = runner.invoke(app, ["pipeline", "save", "--region", "portland", "--force"])
    assert result.exit_code == 1
    assert result.stderr == (
        f"missing input {sidecar}: run `worst-route pipeline cleanup --region portland` first\n"
    )


def test_save_needs_its_inputs(data_dir: Path) -> None:
    cleaned_build(str(FIXTURES / "culdesac.osm"))
    code, output = pipeline("save", "--region", "portland")
    assert code == 1 and "enrich/manifest.json" in output and "pipeline enrich --region" in output
    assert pipeline("enrich", "--region", "portland")[0] == 0
    code, output = pipeline("save", "--region", "portland")
    assert code == 1 and "tiers.json" in output and "pipeline tiers --region" in output


def test_plan_on_a_build_the_pipeline_saved(data_dir: Path, tmp_path: Path) -> None:
    saved_build(str(FIXTURES / "grid.osm"), allow_fragmented=True)
    route = tmp_path / "route.json"
    result = runner.invoke(
        app,
        [
            "plan",
            "--region",
            "portland",
            "--from",
            "45.5000,-122.6000",  # the grid's south-west corner
            "--to",
            "45.5045,-122.5936",  # its north-east corner
            "--seed",
            "1",
            "--budget",
            "5",
            "--out",
            str(route),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "validator: ok (" in result.stdout
    ratio = float(result.stdout.split("ratio: ")[1].split("x")[0])
    assert ratio > 1.0
    result = runner.invoke(app, ["validate", str(route)])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().endswith("valid")


def test_qa_after_save(data_dir: Path) -> None:
    directory = saved_build(str(FIXTURES / "divided.osm"))
    code, output = pipeline("qa", "--region", "portland")
    assert code == 0, output
    assert output.splitlines() == [
        f"qa b1: wrote {directory / 'qa'} (qa.json and 4 layers)",
        "twin coverage: n/a (at least 0.95): pass",
        "SCC retained: 1.0000 (at least 0.98): pass",
        "SCC largest dropped: 0 (at most 200): pass",
        *(f"T{t} retained: 1.0000 (at least 0.95): pass" for t in range(5)),
        "qa passed",
    ]
    assert sorted(p.name for p in (directory / "qa").iterdir()) == [
        "dropped_components.geojson",
        "qa.json",
        "qa.json.inputs.json",
        "superjunctions.geojson",
        "tiers.geojson",
        "twins.geojson",
    ]
    code, output = pipeline("qa", "--region", "portland")
    assert code == 0 and output.startswith("skipped: QA of build b1 is up to date")
    assert output.strip().endswith("qa passed")
    code, output = pipeline("qa", "--region", "portland", "--force")
    assert code == 0 and output.startswith("qa b1: wrote ")


def test_qa_of_a_fragmented_build(data_dir: Path) -> None:
    saved_build(str(FIXTURES / "diamond.osm"), allow_fragmented=True)
    for _ in range(2):  # then skipped: the stored result fails again
        result = runner.invoke(app, ["pipeline", "qa", "--region", "portland"])
        assert result.exit_code == 1
        assert "T0 retained: 0.2000 (at least 0.95): FAIL" in result.stdout
        assert result.stderr == "qa failed: T0 retained, T1 retained, T2 retained, T3 retained\n"
    assert result.stdout.startswith("skipped: ")


def test_qa_refuses_a_stale_build(data_dir: Path) -> None:
    saved_build(str(FIXTURES / "divided.osm"))
    # Another extract under the same build id, rerun up to enrich: save refuses, and so does qa.
    source = str(FIXTURES / "culdesac.osm")
    assert pipeline("filter", "--region", "portland", "--source", source)[0] == 0
    for step in ("build", "cleanup", "enrich"):
        assert pipeline(step, "--region", "portland", "--build-id", "b1")[0] == 0
    assert runner.invoke(app, ["pipeline", "save", "--region", "portland"]).exit_code == 1
    result = runner.invoke(app, ["pipeline", "qa", "--region", "portland"])
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr == (
        "stage tiers is stale: rerun `worst-route pipeline tiers --region portland`\n"
    )
    assert pipeline("tiers", "--region", "portland")[0] == 0
    result = runner.invoke(app, ["pipeline", "qa", "--region", "portland"])
    assert result.exit_code == 1
    assert result.stderr == (
        "stage save is stale: rerun `worst-route pipeline save --region portland`\n"
    )
    assert pipeline("save", "--region", "portland", "--force")[0] == 0
    code, output = pipeline("qa", "--region", "portland")
    assert code == 0 and output.strip().endswith("qa passed")


def test_qa_needs_a_saved_build(data_dir: Path) -> None:
    cleaned_build(str(FIXTURES / "culdesac.osm"))
    code, output = pipeline("qa", "--region", "portland")
    assert code == 1 and "meta.json" in output and "pipeline save --region" in output
    directory = saved_build(str(FIXTURES / "culdesac.osm"))
    (directory / "stages" / "enrich.json").unlink()
    code, output = pipeline("qa", "--region", "portland")
    assert code == 1 and "enrich.json" in output and "pipeline enrich --region" in output


def test_qa_of_a_build_without_an_array_names_the_save_to_run(data_dir: Path) -> None:
    directory = saved_build(str(FIXTURES / "divided.osm"))
    (directory / "arrays" / "node_roundabouts.npy").unlink()
    result = runner.invoke(app, ["pipeline", "qa", "--region", "portland"])
    assert (result.exit_code, result.stdout) == (1, "")
    assert result.stderr == (
        f"the build at {directory} lacks the array node_roundabouts: rebuild it with "
        "`worst-route pipeline save --force --region portland`\n"
    )
    assert pipeline("save", "--region", "portland", "--force")[0] == 0  # the line's remedy
    code, output = pipeline("qa", "--region", "portland")
    assert code == 0 and output.strip().endswith("qa passed")


def test_a_build_another_pipeline_wrote_names_the_rebuild(data_dir: Path) -> None:
    # A build pipeline 1 wrote, lacking the array it did not know: neither a plain save nor qa
    # treats it as current, and both name the rebuild from filter, which gives a new build id.
    directory = saved_build(str(FIXTURES / "divided.osm"))
    meta = json.loads((directory / "meta.json").read_text())
    (directory / "meta.json").write_text(json.dumps({**meta, "pipeline_version": "1"}))
    (directory / "arrays" / "node_roundabouts.npy").unlink()
    line = (
        f"the build at {directory} was written by pipeline 1 (now {PIPELINE_VERSION}): "
        "rebuild the region with `worst-route pipeline filter --region portland` and the steps "
        "after it\n"
    )
    for step in ("save", "qa"):
        result = runner.invoke(app, ["pipeline", step, "--region", "portland"])
        assert (result.exit_code, result.stdout, result.stderr) == (1, "", line), step
