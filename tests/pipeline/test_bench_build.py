"""`tools/bench_build.py` on a fixture: each step run in a process of its own and measured (time,
peak memory, the files it wrote), a skipped step reported as such, the run ended by the first
step that fails, with its output kept, and an unknown step refused before anything runs."""

import argparse
import importlib.util
import json
import sys
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


def test_save_counts_every_part_of_the_build(data_dir: Path) -> None:
    build = layout.build_dir("portland", "b1")
    for name in ("arrays/node_x.npy", "geometry/coords.npy", "strings.json", "meta.json"):
        (build / name).parent.mkdir(parents=True, exist_ok=True)
        (build / name).write_bytes(b"x" * 10)
    layout.latest_file("portland").write_text("b1\n")
    sizes = bench().outputs("save", "portland")
    assert sizes == {
        str(build / n): 10 for n in ("arrays", "geometry", "strings.json", "meta.json")
    }


def test_the_watchdog_stops_a_step_past_its_memory(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tool, out = bench(), tmp_path / "bench.json"
    tool.WATCH_INTERVAL_S = 0.01  # sample often: a fixture's step lasts a fraction of a second
    source = str(FIXTURES / "divided.osm")
    argv = ["--region", "portland", "--source", source, "--steps", "filter,build,cleanup"]
    assert tool.main([*argv, "--max-rss-gb", "0.001", "--out", str(out)]) != 0
    # The first step the watchdog samples past 1 MB is stopped, and the run with it.
    *done, stopped = json.loads(out.read_text())["steps"]
    assert all(step["exit_code"] == 0 and "killed_rss_mb" not in step for step in done)
    assert stopped["exit_code"] < 0  # by a signal
    assert stopped["killed_rss_mb"] > 1 and stopped["killed_after_s"] >= 0
    assert f"| {stopped['step']} |" in capsys.readouterr().out


def test_the_watchdog_never_reaps_a_step(monkeypatch: pytest.MonkeyPatch) -> None:
    # Steps that end at once, sampled every half millisecond: the watchdog must leave the
    # reaping to run_step's os.wait4, or that raises ChildProcessError now and then.
    tool = bench()
    monkeypatch.setattr(tool, "command", lambda: [sys.executable, "-c", "print('done')"])
    monkeypatch.setattr(tool, "outputs", lambda step, region: {})
    monkeypatch.setattr(tool, "WATCH_INTERVAL_S", 0.0005)
    args = argparse.Namespace(
        region="portland", source=None, force=False, allow_fragmented=False, max_rss_gb=100.0
    )
    rows = [tool.run_step("filter", args) for _ in range(200)]
    assert all(row["exit_code"] == 0 and "killed_rss_mb" not in row for row in rows)


def test_a_stopped_step_unwinds_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Past the limit the step gets SIGTERM, on which a pipeline step exits as on an exception
    # (`cli.exit_on_sigterm`), so its temporary files go. This holds, as do the other watchdog
    # tests, when the test runs with SIGINT ignored, as a shell's background job starts (checked
    # by running `( uv run pytest tests/pipeline/test_bench_build.py ) &` from a script).
    tool = bench()
    script = (
        "import sys, tempfile, time\nfrom worst_route.cli import exit_on_sigterm\n"
        "exit_on_sigterm()\nd = tempfile.TemporaryDirectory(dir=sys.argv[1])\n"
        "b = bytearray(80_000_000)\nwith d:\n print('up', flush=True); time.sleep(30)"
    )
    monkeypatch.setattr(tool, "command", lambda: [sys.executable, "-c", script, str(tmp_path)])
    monkeypatch.setattr(tool, "step_argv", lambda step, args: [])
    monkeypatch.setattr(tool, "outputs", lambda step, region: {})
    monkeypatch.setattr(tool, "WATCH_INTERVAL_S", 0.01)
    args = argparse.Namespace(
        region="portland", source=None, force=False, allow_fragmented=False, max_rss_gb=0.05
    )
    row = tool.run_step("filter", args)
    assert row["killed_rss_mb"] > 50 and row["exit_code"] != 0
    assert list(tmp_path.iterdir()) == []  # the step removed its temporary directory


def test_a_step_is_not_started_without_the_disk_it_asks_for(data_dir: Path, tmp_path: Path) -> None:
    tool, out = bench(), tmp_path / "bench.json"
    source = str(FIXTURES / "divided.osm")
    argv = ["--region", "portland", "--source", source, "--steps", "filter"]
    assert tool.main([*argv, "--out", str(out)]) == 0
    (step,) = json.loads(out.read_text())["steps"]
    assert step["free_gb"] > 0  # recorded before every step
    assert tool.main([*argv, "--min-free-gb", "1e9", "--out", str(out)]) == tool.DISK_STOP_CODE
    (step,) = json.loads(out.read_text())["steps"]
    assert step["not_started"] and step["outputs"] == {} and step["wall_s"] == 0.0


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
