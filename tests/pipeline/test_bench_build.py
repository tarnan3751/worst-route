"""`tools/bench_build.py` on a fixture: each step run in a process of its own and measured (time,
peak memory, the files it wrote), a skipped step reported as such, the run ended by the first
step that fails, with its output kept, and an unknown step refused before anything runs."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from worst_route.pipeline import layout

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "osm"


def bench() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench_build", ROOT / "tools" / "bench_build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_step_is_measured(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tool, out = bench(), tmp_path / "bench.json"
    source = str(FIXTURES / "divided.osm")
    argv = ["--region", "portland", "--source", source, "--steps", "filter,build", "--out"]
    assert tool.main([*argv, str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["build_id"] == layout.latest_file("portland").read_text().strip()
    assert [s["step"] for s in report["steps"]] == ["filter", "build"]
    filter_, build = report["steps"]
    assert filter_["argv"] == ["pipeline", "filter", "--region", "portland", "--source", source]
    assert build["argv"] == ["pipeline", "build", "--region", "portland"]
    for step in report["steps"]:
        assert step["exit_code"] == 0 and not step["skipped"]
        assert step["wall_s"] > 0 and step["max_rss_mb"] > 10  # an interpreter, at least
        assert step["outputs"] and all(size > 0 for size in step["outputs"].values())
    assert set(filter_["outputs"]) == {
        str(layout.roads_pbf("portland")),
        str(layout.pois_parquet("portland")),
    }
    stages = layout.build_dir("portland", report["build_id"]) / "stages"
    raw = stages / "build"
    assert build["outputs"] == {str(raw): sum(p.stat().st_size for p in raw.iterdir())}
    assert build["output"][1] == "ways: read 12, kept 12; segments 23, nodes 19, edges 30"
    printed = capsys.readouterr().out
    assert "| Step | Wall | CPU (user + sys) | Peak RSS | Output | Result |" in printed
    assert "| filter | " in printed and "| build | " in printed and f"wrote {out}" in printed

    # up to date: the CLI skips it, and the row says so; --force runs it again
    assert tool.main(["--region", "portland", "--steps", "build", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["steps"][0]["skipped"]
    argv = ["--region", "portland", "--steps", "build", "--force", "--out", str(out)]
    assert tool.main(argv) == 0
    (step,) = json.loads(out.read_text())["steps"]
    assert step["argv"][-1] == "--force" and not step["skipped"]


def test_the_first_failing_step_ends_the_run(data_dir: Path, tmp_path: Path) -> None:
    tool, out = bench(), tmp_path / "bench.json"
    argv = ["--region", "portland", "--steps", "cleanup,enrich", "--allow-fragmented"]
    assert tool.main([*argv, "--out", str(out)]) == 1
    report = json.loads(out.read_text())
    (step,) = report["steps"]
    assert step["step"] == "cleanup" and step["exit_code"] == 1 and step["outputs"] == {}
    assert step["argv"][-1] == "--allow-fragmented"
    assert step["output"][0].startswith("missing input ") and "pipeline build" in step["output"][0]
    assert report["build_id"] is None


def test_an_unknown_step_is_refused(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "bench.json"
    argv = ["--region", "portland", "--steps", "build,download", "--out", str(out)]
    assert bench().main(argv) == 2
    assert capsys.readouterr().err.startswith("unknown step 'download'; known: filter, build, ")
    assert not out.exists()
