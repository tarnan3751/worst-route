import json
import math
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from worst_route.cli import _seconds, app
from worst_route.engine import ENGINE_VERSION
from worst_route.graph.data import GraphData
from worst_route.graph.store import write_build
from worst_route.graph.testing import make_graph

from .graphs import X0, Y0, at, at_node, lattice, left_only

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


def test_on_a_build_with_turn_tables_both_backends_plan_the_same_route(
    data_dir: Path, tmp_path: Path
) -> None:
    # auto is csr there too; both plan the same route.json (but the backend and the time),
    # which validates on both
    straight = [
        ((44, 45), (45, 46)),
        ((46, 45), (45, 44)),
        ((35, 45), (45, 55)),
        ((55, 45), (45, 35)),
    ]
    g = save(data_dir, lattice(10, 10, transitions=straight, rounded_lonlat=True), "b1")
    trip = ["--from", latlon(g, 0), "--to", latlon(g, 99), "--seed", "4"]
    code, stdout, stderr = plan(*trip, "--max-iterations", "20")
    assert code == 0, stderr
    assert "backend: csr" in stdout.splitlines()
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
    assert docs["python"]["run"]["accepted"] == docs["csr"]["run"]["accepted"] > 0


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
    doc["metrics"]["length_m"] -= 10
    doc["metrics"]["items"]["bridge"] = 999  # every route.json counts the items
    out.write_text(json.dumps(doc))
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1
    assert "metrics: FAILED: items reported {" in result.stdout and "'bridge': 999" in result.stdout

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


def test_a_build_saved_before_an_array_existed_is_one_line(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, replace(GRID, meta={**GRID.meta, "pipeline_version": "1"}), "b1")
    out = tmp_path / "route.json"
    trip = ["--from", latlon(GRID, 0), "--to", latlon(GRID, 9), "--max-iterations", "20"]
    assert plan(*trip, "--out", str(out))[0] == 0
    path = data_dir / "graphs" / "portland" / "b1"
    (path / "arrays" / "node_roundabouts.npy").unlink()
    line = (
        f"the build at {path} was written by pipeline 1 (now 2): rebuild the region with "
        "`worst-route pipeline filter --region portland` and the steps after it\n"
    )
    assert plan(*trip) == (1, "", line)
    result = runner.invoke(app, ["validate", str(out)])
    assert (result.exit_code, result.stderr) == (1, line)


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


def test_a_target_is_planned_printed_and_recorded(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    trip = ["--from", latlon(GRID, 0), "--to", latlon(GRID, 99), "--seed", "3"]
    out = tmp_path / "route.json"
    code, stdout, stderr = plan(*trip, "--target", "ratio:3", "--out", str(out))
    assert code == 0, stdout + stderr
    lines = stdout.splitlines()
    assert "target: ratio 3x → achieved 3.00x (within ±2 %)" in lines
    assert any(line.startswith("search: ") and line.endswith("stopped: target") for line in lines)
    doc = json.loads(out.read_text())
    assert doc["request"]["target"] == {"kind": "ratio", "value": 3.0}
    assert doc["metrics"]["target"] == {
        "kind": "ratio",
        "value": 3.0,
        "achieved": 3.0,
        "within_tolerance": True,
    }
    assert runner.invoke(app, ["validate", str(out)]).exit_code == 0

    code, stdout, _ = plan(*trip, "--target", "duration:8m")
    assert code == 0
    assert "target: duration 8 min 0 s → achieved 8 min 6 s (within ±2 %)" in stdout.splitlines()
    code, stdout, _ = plan(*trip)
    assert code == 0 and "target:" not in stdout


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("9h", 32400), ("90m", 5400), ("2h30m", 9000), ("45s", 45), ("1.5h", 5400), ("600", 600)],
)
def test_a_duration_is_read_in_hours_minutes_or_seconds(text: str, seconds: float) -> None:
    assert _seconds(text) == seconds


def test_a_bad_target_is_refused(data_dir: Path) -> None:
    save(data_dir, GRID, "b1")
    trip = ["--from", latlon(GRID, 0), "--to", latlon(GRID, 99)]
    for target in ("ratio:x", "ratio:1", "ratio", "duration:9x", "duration:", "duration:8d",
                   "duration:9h:", "max:3", "speed:3", "duration:200h"):  # fmt: skip
        code, stdout, stderr = plan(*trip, "--target", target)
        assert code == 2 and stdout == "", target
        assert "target" in stderr


def test_a_round_trip_has_no_ratio_target(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    loop = ["--from", latlon(GRID, 45), "--to", latlon(GRID, 45)]
    code, stdout, stderr = plan(*loop, "--target", "ratio:3")
    assert (code, stdout, stderr) == (2, "", "invalid --target: a round trip has no ratio target\n")
    out = tmp_path / "loop.json"
    assert plan(*loop, "--seed", "1", "--max-iterations", "20", "--out", str(out))[0] == 0
    code, stdout, stderr = plan("--base", str(out), "--target", "ratio:3")
    assert code == 2 and "invalid --target: a round trip has no ratio target" in stderr
    assert plan("--base", str(out), "--target", "duration:30m", "--max-iterations", "20")[0] == 0


def test_an_illegal_route_is_a_base_file_s_fault_only_with_base(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(data_dir, GRID, "b1")

    def broken(*args: object, **kwargs: object) -> None:
        raise IndexError("an engine bug")

    monkeypatch.setattr("worst_route.engine.plan.plan_route", broken)
    result = runner.invoke(app, ["plan", "--region", "portland", "--from", latlon(GRID, 0),
                                 "--to", latlon(GRID, 99)])  # fmt: skip
    assert isinstance(result.exception, IndexError) and "not a legal route" not in result.stderr


def test_base_continues_a_route_toward_a_new_target(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    first, second = tmp_path / "worst.json", tmp_path / "less.json"
    trip = ["--from", latlon(GRID, 0), "--to", latlon(GRID, 99)]
    code, *_ = plan(*trip, "--seed", "1", "--out", str(first))
    assert code == 0
    worst = json.loads(first.read_text())
    assert worst["metrics"]["ratio"] > 5

    code, stdout, stderr = plan(
        "--base", str(first), "--target", "ratio:4", "--seed", "2", "--out", str(second)
    )
    assert code == 0, stdout + stderr
    lines = stdout.splitlines()
    assert f"base: {first}: 9.80 km, 14 min 42 s, 5.44x" in lines
    assert "target: ratio 4x → achieved 4.00x (within ±2 %)" in lines
    doc = json.loads(second.read_text())
    assert doc["request"]["stops"] == worst["request"]["stops"] and doc["seed"] == 2
    assert doc["stop_idx"][-1] == len(doc["nodes"]) - 1
    assert runner.invoke(app, ["validate", str(second)]).exit_code == 0


def test_base_takes_its_stops_and_scale_from_its_file(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    first = tmp_path / "worst.json"
    trip = ["--from", latlon(GRID, 0), "--to", latlon(GRID, 99)]
    assert plan(*trip, "--seed", "1", "--max-iterations", "50", "--out", str(first))[0] == 0
    takes = "--base takes the stops, tier and leash from its route: drop"
    for given, named in (
        (trip, "--from, --to"),
        (["--via", latlon(GRID, 45)], "--via"),
        (["--tier", "auto"], "--tier"),
        (["--leash-km", "3"], "--leash-km"),
    ):
        refused = plan("--base", str(first), *given)
        assert refused == (2, "", f"{takes} {named}\n")  # one plain line, whatever the terminal
    refused = plan("--to", latlon(GRID, 99))
    assert refused == (2, "", "--from and --to are needed, unless --base names a route\n")

    doc = json.loads(first.read_text())
    for change, message in (
        ({"engine_version": "1"}, "was made by engine version 1"),
        ({"region": "oregon"}, "was planned on oregon, not portland"),
        ({"graph_version": "0" * 64}, "was planned on graph_version 000000000000"),
        ({"nodes": doc["nodes"][:3] + doc["nodes"][4:]}, "is not a legal route: IndexError"),
        ({"nodes": doc["nodes"][:3] + [doc["nodes"][1]] + doc["nodes"][4:]}, "BrokenChain"),
        ({"stop_idx": [0, 3]}, "cannot plan: the base route's stops are not the ones"),
    ):
        edited = tmp_path / "edited.json"
        edited.write_text(json.dumps({**doc, **change}))
        code, stdout, stderr = plan("--base", str(edited))
        assert code == 1 and stdout == "" and message in stderr, stderr


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


def test_the_objective_and_the_turn_constraints(data_dir: Path, tmp_path: Path) -> None:
    save(data_dir, GRID, "b1")
    trip = ("--from", latlon(GRID, 22), "--to", latlon(GRID, 77), "--seed", "3")
    out = tmp_path / "time.json"
    code, stdout, stderr = plan(*trip, "--objective", "time", "--no-left", "--out", str(out))
    assert code == 0, stdout + stderr
    lines = stdout.splitlines()
    assert lines[2] == "objective: time, few left turns"
    # the experienced time's ratio, with its drive and delays (none on this lattice)
    (timed,) = [line for line in lines if line.startswith("time ratio: ")]
    assert re.fullmatch(r"time ratio: \d+\.\d\dx \(drive (.+) \+ delays 0 min 0 s = \1\)", timed)
    doc = json.loads(out.read_text())
    assert doc["request"]["objective"] == "time"
    assert doc["request"]["constraints"] == {"no_left": True, "no_right": False, "strict": False}
    turns = doc["metrics"]["turns"]
    line = f"turns: {turns['left']} left, {turns['right']} right, {turns['straight']} straight, "
    assert f"{line}{turns['u_turn']} u-turns; delays 0 min 0 s" in lines
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 0 and result.stdout.splitlines()[-1] == "valid", result.output

    code, stdout, _ = plan(*trip, "--no-right", "--strict")
    assert code == 0 and "objective: distance, no right turns" in stdout.splitlines()
    code, stdout, _ = plan(*trip, "--out", str(out))
    assert code == 0 and not any(line.startswith("objective:") for line in stdout.splitlines())
    assert "constraints" not in json.loads(out.read_text())["request"]
    for bad in (("--no-left", "--no-right"), ("--objective", "slowest")):
        code, stdout, _ = plan(*trip, *bad)
        assert code == 2 and stdout == "", bad
    assert plan(*trip, "--strict") == (2, "", "--strict needs --no-left or --no-right\n")


def test_a_strict_constraint_no_route_keeps_is_one_line(data_dir: Path) -> None:
    g = save(data_dir, left_only(), "t1")
    trip = ("--from", latlon(g, 0), "--to", latlon(g, 14), "--seed", "1")
    assert plan(*trip, "--no-left")[0] == 0  # soft: the left turn it cannot avoid
    assert plan(*trip, "--no-left", "--strict") == (
        1,
        "",
        "cannot plan: no route from stop 0 to stop 1 without repeating a road, after moving "
        "stop 1 up to 10 times, under the strict no-left constraint; try without --strict\n",
    )


def test_base_takes_its_objective_and_constraints_from_its_file(
    data_dir: Path, tmp_path: Path
) -> None:
    save(data_dir, GRID, "b1")
    first = tmp_path / "first.json"
    trip = ("--from", latlon(GRID, 22), "--to", latlon(GRID, 77), "--seed", "1")
    assert plan(*trip, "--objective", "time", "--no-left", "--out", str(first))[0] == 0
    second = tmp_path / "second.json"
    code, stdout, stderr = plan("--base", str(first), "--seed", "2", "--out", str(second))
    assert code == 0, stdout + stderr
    request = json.loads(second.read_text())["request"]
    assert request["objective"] == "time" and request["constraints"]["no_left"] is True
    assert plan("--base", str(first), "--no-right", "--strict") == (
        2,
        "",
        "--base takes the objective and constraints from its route: drop --no-right, --strict\n",
    )


def test_validate_proves_a_strict_constraint(data_dir: Path, tmp_path: Path) -> None:
    # a soft no-left route with a left turn, its file edited to claim a strict constraint
    save(data_dir, GRID, "b1")
    out = tmp_path / "soft.json"
    trip = ("--from", latlon(GRID, 22), "--to", latlon(GRID, 77), "--seed", "3")
    assert plan(*trip, "--no-left", "--out", str(out))[0] == 0
    doc = json.loads(out.read_text())
    assert doc["metrics"]["turns"]["left"] > 0
    assert runner.invoke(app, ["validate", str(out)]).exit_code == 0
    doc["request"]["constraints"]["strict"] = True
    out.write_text(json.dumps(doc))
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 1
    assert any(
        line.startswith("constraints: FAILED at ") and "left turn" in line
        for line in result.stdout.splitlines()
    ), result.output
