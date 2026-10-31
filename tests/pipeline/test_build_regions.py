"""`tools/build_regions.py` on a fixture extract: a region built from its extract to its QA, pruned
and given its places; one too fragmented rebuilt with `--allow-fragmented`, its QA failure
recorded and its stages kept; a region done before not run again but offered to `prune`; the
batch ended by the disk floor; the regions it is given or the states, smallest extract first;
and what its table says of a fragmented build."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.pipeline.test_cli_steps import raw_extract
from tests.presentation.test_places import _census_zips
from worst_route.config import REGIONS, STATES
from worst_route.pipeline import layout

ROOT = Path(__file__).parents[2]


def batch() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_regions", ROOT / "tools" / "build_regions.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_region_is_built_checked_pruned_and_given_its_places(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_extract("oregon", "divided")  # portland's extract
    _census_zips(data_dir / "aux" / "census")
    out = tmp_path / "batch.json"
    assert batch().main(["--regions", "portland", "--out", str(out)]) == 0
    (row,) = json.loads(out.read_text())["regions"]
    assert row["region"] == "portland" and row["qa"] == "passed" and not row["fragmented"]
    assert row["build_id"] == layout.latest_file("portland").read_text().strip()
    assert row["nodes"] > 0 and row["edges"] > 0 and row["megabytes"] >= 0
    assert row["wall_s"] > 0 and row["max_mem_mb"] > 10 and row["failed_step"] is None
    report = json.loads(Path(row["reports"][0]).read_text())
    assert [s["step"] for s in report["steps"]] == [
        "filter", "build", "cleanup", "enrich", "tiers", "save", "qa"
    ]  # fmt: skip
    assert row["pruned"]
    assert row["prune"].startswith(f"prune {row['build_id']}: removed build, cleanup, enrich")
    directory = layout.build_dir("portland", row["build_id"])
    assert not (directory / "stages" / "cleanup").exists() and (directory / "arrays").is_dir()
    assert row["places"].startswith("wrote ") and "portland.parquet" in row["places"]
    printed = capsys.readouterr().out
    assert f"| portland | {row['build_id']} | {row['nodes']:,} | " in printed
    assert printed.rstrip().endswith(f"wrote {out}")

    # Run again: the region reached its QA, so it is not run again.
    assert batch().main(["--regions", "portland", "--out", str(out)]) == 0
    (again,) = json.loads(out.read_text())["regions"]
    assert again["done_before"] and again["build_id"] == row["build_id"]
    assert again["pruned"]
    assert again["prune"] == f"skipped: build {row['build_id']} has no stage data to prune"
    assert again["nodes"] == row["nodes"] and again["wall_s"] == row["wall_s"]


def test_a_region_too_fragmented_is_rebuilt_with_allow_fragmented(
    data_dir: Path, tmp_path: Path
) -> None:
    raw_extract("oregon", "grid")  # its tiers keep too few of their roads
    _census_zips(data_dir / "aux" / "census")
    out = tmp_path / "batch.json"
    assert batch().main(["--regions", "portland", "--out", str(out)]) == 0
    (row,) = json.loads(out.read_text())["regions"]
    assert row["fragmented"] and len(row["reports"]) == 2
    first = json.loads(Path(row["reports"][0]).read_text())
    assert first["steps"][-1]["step"] == "tiers" and first["steps"][-1]["exit_code"] == 1
    assert row["qa"] == "failed" and row["qa_failed"] == {"T3 retained": 0.6875}
    assert row["failed_step"] is None
    assert not row["pruned"]  # QA failed beyond the connectivity rule: the stages stay
    assert row["prune"].startswith(f"prune refused: the QA of build {row['build_id']} failed")
    assert (layout.build_dir("portland", row["build_id"]) / "stages" / "cleanup").is_dir()
    assert row["places"].startswith("wrote ")


def test_the_disk_floor_ends_the_batch(data_dir: Path, tmp_path: Path) -> None:
    raw_extract("oregon", "divided")
    out = tmp_path / "batch.json"
    argv = ["--regions", "portland", "oregon", "--min-free-gb", "1e9", "--out", str(out)]
    assert batch().main(argv) == 3  # bench_build.py's DISK_STOP_CODE
    report = json.loads(out.read_text())
    assert [r["region"] for r in report["regions"]] == ["oregon"]  # oregon's extract sorts first
    assert report["halted"]["region"] == "oregon" and report["halted"]["not_started"]
    assert report["halted"]["step"] == "filter"


def test_the_regions_or_the_states_smallest_extract_first(data_dir: Path) -> None:
    tool = batch()
    with pytest.raises(SystemExit):  # one or the other
        tool.parse_args([])
    assert tool.parse_args(["--regions", "portland", "oregon"]).regions == ["portland", "oregon"]
    assert tool.parse_args(["--states"]).states
    regions = tool.state_regions()
    assert sorted(regions) == sorted(s for s in STATES if s not in ("alaska", "hawaii"))
    assert all(REGIONS[r].states == (r,) for r in regions)
    for state, size in (("vermont", 30), ("delaware", 10)):
        layout.raw_pbf(state).parent.mkdir(parents=True, exist_ok=True)
        layout.raw_pbf(state).write_bytes(b"x" * size)
    order = sorted(["texas", "vermont", "delaware"], key=tool.extract_bytes)
    assert order == ["delaware", "vermont", "texas"]  # texas's extract is missing: last


def test_the_table_says_what_a_fragmented_build_dropped() -> None:
    base = {"region": "maine", "build_id": "b1", "nodes": 1, "edges": 2, "km": 3.0}
    base |= {"megabytes": 1.0, "geometry_megabytes": 0.5, "wall_s": 10.0, "max_mem_mb": 2000.0}
    base |= {"fragmented": True, "qa": "failed"}
    rows = [
        {**base, "qa_failed": {"SCC retained": 0.9642, "SCC largest dropped": 351}, "pruned": True},
        {**base, "qa_failed": {"SCC retained": 0.966}, "pruned": True},
        {**base, "qa_failed": {"twin coverage": 0.927, "T0 retained": 0.936}, "pruned": False},
    ]
    lines = batch().table(rows)
    assert lines[2] == (
        "| maine | b1 | 1 | 2 | 3 | 1.5 MB | 10 s | 2.00 GB | "
        "fragmented: retained 0.964, largest 351 nodes, pruned |"
    )
    assert lines[3].endswith("| fragmented: retained 0.966, pruned |")
    assert lines[4].endswith("| failed: twin coverage 0.927, T0 retained 0.936 (fragmented) |")
