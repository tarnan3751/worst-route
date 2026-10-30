import json
import math
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from worst_route.cli import app
from worst_route.engine import ENGINE_VERSION
from worst_route.graph.data import GraphData
from worst_route.graph.store import write_build
from worst_route.graph.testing import make_graph

from .graphs import X0, Y0, at, at_node, lattice

runner = CliRunner()
GRID = lattice(10, 10, rounded_lonlat=True)


def save(data_dir: Path, g: GraphData, build_id: str, latest: bool = True) -> GraphData:
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": build_id})
    write_build(g, data_dir / "graphs" / "portland" / build_id)
    if latest:
        (data_dir / "graphs" / "portland" / "latest").write_text(f"{build_id}\n")
    return g


def latlon(g: GraphData, v: int) -> str:
    lat, lon = at_node(g, v)
    return f"{lat},{lon}"


def plan(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(app, ["plan", "--region", "portland", *args])
    return result.exit_code, result.stdout, result.stderr


def test_plan_prints_a_validated_route_and_writes_its_files(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    out, geo = tmp_path / "route.json", tmp_path / "route.geojson"
    code, stdout, stderr = plan(
        "--from", latlon(GRID, 0), "--to", latlon(GRID, 99), "--via", latlon(GRID, 45),
        "--seed", "3", "--max-iterations", "200", "--out", str(out), "--geojson", str(geo),
    )  # fmt: skip
    assert code == 0, stdout + stderr
    lines = stdout.splitlines()
    assert lines[0].startswith("build: portland/b1 (graph_version ")
    assert lines[1] == "request: tier T4, leash 3.0 km, seed 3, budget 20 s or 200 attempts"
    assert lines[2].startswith("stop 0 (start): node 0 at ") and lines[2].endswith("moved 0 m")
    assert lines[3].startswith("stop 1 (via): node 45 at ")
    assert lines[4].startswith("stop 2 (end): node 99 at ")
    assert lines[5].startswith("baseline: 1.80 km, ")
    assert any(line.startswith("ratio: ") and line.endswith("x") for line in lines)
    assert any(line.startswith("search: 200 attempts, ") for line in lines)
    checks = "chain, vertex_simple, edge_simple, osm_ids, transitions, stops, constraints, metrics"
    assert f"validator: ok ({checks})" in lines
    doc = json.loads(out.read_text())
    assert doc["seed"] == 3 and doc["region"] == "portland" and doc["build_id"] == "b1"
    assert json.loads(geo.read_text())["type"] == "FeatureCollection"

    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-1] == "valid"
    assert "metrics: ok" in result.stdout


def test_the_same_seed_plans_the_same_route(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    files = []
    for k in range(2):
        out = tmp_path / f"r{k}.json"
        code, *_ = plan(
            "--from",
            latlon(GRID, 0),
            "--to",
            latlon(GRID, 99),
            "--seed",
            "8",
            "--max-iterations",
            "100",
            "--out",
            str(out),
        )
        assert code == 0
        files.append(json.loads(out.read_text()))
    assert files[0]["nodes"] == files[1]["nodes"]


def test_the_backend_is_chosen_printed_and_recorded(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    trip = ["--from", latlon(GRID, 0), "--to", latlon(GRID, 99), "--seed", "4"]
    code, stdout, _ = plan(*trip, "--max-iterations", "20")
    assert code == 0 and "backend: csr" in stdout.splitlines()  # auto, when numba imports
    docs = {}
    for name in ("python", "csr"):
        out = tmp_path / f"{name}.json"
        code, stdout, stderr = plan(
            *trip, "--max-iterations", "150", "--backend", name, "--out", str(out)
        )
        assert code == 0, stdout + stderr
        assert f"backend: {name}" in stdout.splitlines()
        docs[name] = json.loads(out.read_text())
        assert docs[name]["run"]["backend"] == name
        result = runner.invoke(app, ["validate", str(out), "--backend", name])
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines()[1] == f"backend: {name}"
    for key in ("nodes", "edges", "stop_idx", "stops", "metrics"):
        assert docs["python"][key] == docs["csr"][key]
    assert docs["python"]["run"]["accepted"] == docs["csr"]["run"]["accepted"]

    code, _, stderr = plan(*trip, "--backend", "numpy")
    assert code == 2 and "unknown backend 'numpy'" in stderr


def test_the_csr_backend_without_numba_is_one_line(data_dir: Path, tmp_path: Path) -> None:
    # a fresh interpreter in which `import numba` fails (the data_dir fixture's environment is
    # inherited, so the subprocess reads the same build)
    save(data_dir, GRID, "b1")
    no_numba = "import sys; sys.modules['numba'] = None; from worst_route.cli import app; app()"

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", no_numba, *args], capture_output=True, text=True, check=False
        )

    trip = ["--from", latlon(GRID, 0), "--to", latlon(GRID, 99), "--seed", "4"]
    out = tmp_path / "route.json"
    planned = cli(
        "plan", "--region", "portland", *trip, "--max-iterations", "20", "--out", str(out)
    )
    assert planned.returncode == 0, planned.stderr
    assert "backend: python" in planned.stdout.splitlines()  # auto falls back
    for args in (["plan", "--region", "portland", *trip, "--backend", "csr"],
                 ["validate", str(out), "--backend", "csr"]):  # fmt: skip
        failed = cli(*args)
        assert failed.returncode == 1 and failed.stdout == ""
        lines = failed.stderr.splitlines()
        assert len(lines) == 1
        assert lines[0].startswith("the csr backend needs numba, which did not import: ")


def test_on_a_build_with_turn_tables_auto_is_python_and_csr_one_line(
    data_dir: Path, tmp_path: Path
) -> None:
    # the CSR kernels cannot search transition tables yet: auto takes the reference and says
    # so; csr asked for by name is one line on both commands
    straight = [
        ((44, 45), (45, 46)),
        ((46, 45), (45, 44)),
        ((35, 45), (45, 55)),
        ((55, 45), (45, 35)),
    ]
    g = save(data_dir, lattice(10, 10, transitions=straight, rounded_lonlat=True), "b1")
    trip = ["--from", latlon(g, 0), "--to", latlon(g, 99), "--seed", "4"]
    out = tmp_path / "route.json"
    code, stdout, stderr = plan(*trip, "--max-iterations", "20", "--out", str(out))
    assert code == 0, stderr
    assert "backend: python (csr cannot search turn tables yet)" in stdout.splitlines()
    assert json.loads(out.read_text())["run"]["backend"] == "python"
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[1] == "backend: python (csr cannot search turn tables yet)"
    refusal = "the csr backend cannot search turn tables yet; use --backend python"
    code, stdout, stderr = plan(*trip, "--backend", "csr")
    assert (code, stdout, stderr.splitlines()) == (1, "", [f"cannot plan: {refusal}"])
    result = runner.invoke(app, ["validate", str(out), "--backend", "csr"])
    assert result.exit_code == 1 and result.stderr.splitlines() == [f"cannot validate: {refusal}"]


def test_validate_refuses_a_route_from_another_engine_version(
    data_dir: Path, tmp_path: Path
) -> None:
    save(data_dir, GRID, "b1")
    out = tmp_path / "route.json"
    code, _, stderr = plan(
        "--from", latlon(GRID, 0), "--to", latlon(GRID, 99), "--seed", "4",
        "--max-iterations", "20", "--out", str(out),
    )  # fmt: skip
    assert code == 0, stderr
    doc = json.loads(out.read_text())
    doc["engine_version"] = "1"
    out.write_text(json.dumps(doc))
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr.splitlines() == [
        f"{out} was made by engine version 1; this is version {ENGINE_VERSION}: re-plan"
    ]


def test_a_random_seed_is_printed_and_recorded(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    out = tmp_path / "route.json"
    code, stdout, _ = plan(
        "--from",
        latlon(GRID, 0),
        "--to",
        latlon(GRID, 9),
        "--max-iterations",
        "20",
        "--out",
        str(out),
    )
    assert code == 0
    seed = json.loads(out.read_text())["seed"]
    assert f"seed {seed}," in stdout


def test_validate_catches_a_tampered_route(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    out = tmp_path / "route.json"
    code, *_ = plan(
        "--from",
        latlon(GRID, 0),
        "--to",
        latlon(GRID, 99),
        "--seed",
        "1",
        "--max-iterations",
        "100",
        "--out",
        str(out),
    )
    assert code == 0
    doc = json.loads(out.read_text())
    doc["metrics"]["length_m"] += 10
    out.write_text(json.dumps(doc))
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1
    assert "metrics: FAILED: length_m reported" in result.stdout

    doc["graph_version"] = "0" * 64
    out.write_text(json.dumps(doc))
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1 and "was planned on graph_version 000000000000" in result.stderr

    out.write_text("{}")
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1 and result.stderr.startswith(f"cannot read {out}")


def test_plan_reads_the_named_build(data_dir: Path) -> None:
    save(data_dir, GRID, "b1")
    other = save(data_dir, lattice(4, 4, rounded_lonlat=True), "small", latest=False)
    code, stdout, _ = plan(
        "--build-id",
        "small",
        "--from",
        latlon(other, 0),
        "--to",
        latlon(other, 15),
        "--seed",
        "0",
        "--max-iterations",
        "10",
    )
    assert code == 0 and stdout.startswith("build: portland/small ")


def test_a_missing_build_says_to_run_save(data_dir: Path) -> None:
    code, stdout, stderr = plan("--from", "45.5,-122.6", "--to", "45.6,-122.5")
    assert code == 1 and stdout == ""
    assert (
        stderr.strip()
        == "no build of portland: run `worst-route pipeline save --region portland` first"
    )
    (data_dir / "graphs" / "portland" / "b2").mkdir(parents=True)
    code, _, stderr = plan("--build-id", "b2", "--from", "45.5,-122.6", "--to", "45.6,-122.5")
    assert code == 1 and stderr.startswith("no saved build at ") and "pipeline save" in stderr


def test_a_stop_off_the_map_or_out_of_reach_is_one_line(data_dir: Path) -> None:
    save(data_dir, GRID, "b1")
    code, stdout, stderr = plan("--from", latlon(GRID, 0), "--to", "44.0,-121.0")
    assert code == 1 and stdout == ""
    assert stderr.startswith("cannot plan: no node within 5000 m") and len(stderr.splitlines()) == 1

    # two islands 8 km apart: the end is on the other one
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 8000, Y0), (X0 + 8100, Y0)]
    islands = make_graph(xy, [(0, 1, 100.0), (1, 0, 100.0), (2, 3, 100.0), (3, 2, 100.0)])
    save(data_dir, islands, "islands")
    lat, lon = at(8100, 0)
    code, _, stderr = plan("--from", latlon(islands, 0), "--to", f"{lat},{lon}", "--leash-km", "20")
    assert code == 1
    assert stderr == (
        "cannot plan: no route from stop 0 to stop 1 without repeating a road, "
        "after moving stop 1 up to 10 times\n"
    )


def test_bad_arguments_are_refused(data_dir: Path) -> None:
    save(data_dir, GRID, "b1")
    for args in (
        ["--from", "45.5", "--to", latlon(GRID, 9)],
        ["--from", latlon(GRID, 0), "--to", latlon(GRID, 9), "--tier", "T9"],
        ["--from", latlon(GRID, 0), "--to", latlon(GRID, 9), "--leash-km", "far"],
        ["--from", latlon(GRID, 0), "--to", latlon(GRID, 9), "--leash-km", "-2"],
    ):
        code, stdout, _ = plan(*args)
        assert code == 2 and stdout == ""


def test_validate_re_derives_the_stops(data_dir: Path, tmp_path: Path) -> None:
    # a route cut short and its file re-edited to agree with itself: only the stops betray it
    g = save(data_dir, GRID, "b1")
    out = tmp_path / "route.json"
    code, *_ = plan(
        "--from", latlon(GRID, 0), "--to", latlon(GRID, 99),
        "--seed", "1", "--max-iterations", "60", "--out", str(out),
    )  # fmt: skip
    assert code == 0
    doc = json.loads(out.read_text())
    keep = len(doc["edges"]) // 2
    doc["nodes"], doc["edges"] = doc["nodes"][: keep + 1], doc["edges"][:keep]
    doc["stop_idx"] = [0, keep]
    doc["stops"][-1]["node"] = doc["nodes"][-1]
    edges = np.array(doc["edges"])
    doc["metrics"]["length_m"] = math.fsum(g.edge_length_m[edges].astype(np.float64).tolist())
    doc["metrics"]["time_s"] = math.fsum(g.edge_time_s[edges].astype(np.float64).tolist())
    out.write_text(json.dumps(doc))
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1
    failed = [line for line in result.stdout.splitlines() if "FAILED" in line]
    assert failed == [f"stops: FAILED at 1: stop 1 is node 99, the route has {doc['nodes'][-1]}"]


def test_validate_refuses_an_id_beyond_int32(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    out = tmp_path / "route.json"
    code, *_ = plan(
        "--from",
        latlon(GRID, 0),
        "--to",
        latlon(GRID, 9),
        "--seed",
        "1",
        "--max-iterations",
        "5",
        "--out",
        str(out),
    )
    assert code == 0
    doc = json.loads(out.read_text())
    doc["nodes"][1] = 2**40
    out.write_text(json.dumps(doc))
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr.startswith(f"cannot read {out}: OverflowError")


def test_a_route_that_fails_validation_is_never_printed(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from worst_route.engine import plan as plan_module
    from worst_route.engine.validator import Check, ValidationReport

    save(data_dir, GRID, "b1")

    def failing(*args: object, **kwargs: object) -> ValidationReport:
        return ValidationReport((Check("chain", True), Check("metrics", False, None, "made up")))

    monkeypatch.setattr(plan_module, "validate_route", failing)
    code, stdout, stderr = plan(
        "--from", latlon(GRID, 0), "--to", latlon(GRID, 99), "--seed", "1", "--max-iterations", "10"
    )
    assert code == 1 and stdout == ""
    assert stderr == "the route failed validation (an engine bug): metrics: FAILED: made up\n"
