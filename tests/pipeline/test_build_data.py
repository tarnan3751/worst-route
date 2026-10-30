# The saved build of Portland's latest build id, read through WORST_ROUTE_DATA. Run on demand with
# `uv run pytest -m data`; skipped when that build has not been saved.
import json
import pickle
from pathlib import Path

import numpy as np
import pytest

from tests.pipeline.invariants import check_build, check_saved
from worst_route.config import TIERS
from worst_route.graph.data import GraphData
from worst_route.graph.store import graph_version, read_build
from worst_route.pipeline import layout

pytestmark = pytest.mark.data

type Build = tuple[Path, GraphData]


@pytest.fixture(scope="module")
def build() -> Build:
    latest = layout.latest_file("portland")
    if not latest.exists():
        pytest.skip(f"no Portland build under {latest.parent}")
    directory = layout.build_dir("portland", latest.read_text().strip())
    if not (directory / "meta.json").exists():
        pytest.skip(f"no saved build in {directory}")
    return directory, read_build(directory)


def test_portland_build_is_valid(build: Build) -> None:
    _, data = build
    check_build(data)  # validate() included
    assert graph_version(data) == data.meta["graph_version"]
    assert data.meta["region"] == "portland" and data.meta["sources"][0]["source_timestamp"]


def test_portland_build_holds_its_graph(build: Build) -> None:
    directory, data = build
    with layout.stage_file(directory, "enrich.pkl").open("rb") as f:
        graph = pickle.load(f)
    check_saved(graph, data)


def test_portland_tiers(build: Build) -> None:
    directory, data = build
    stage = json.loads(layout.stage_file(directory, "tiers.json").read_text())
    for t, classes in enumerate(TIERS):
        assert np.flatnonzero(data.tier_node_mask[t]).tolist() == stage["tiers"][t]["node_ids"]
        assert not (data.tier_edge_mask[t] & ~np.isin(data.edge_highway, sorted(classes))).any()
    assert data.tier_node_mask[4].all() and data.tier_edge_mask[4].all()


def test_portland_qa_passes(build: Build) -> None:
    directory, _ = build
    report = layout.qa_file(directory, "qa.json")
    if not report.exists():
        pytest.skip(f"no QA report in {directory}")
    qa = json.loads(report.read_text())
    assert [t["name"] for t in qa["thresholds"] if not t["passed"]] == []
    assert qa["passed"] is True
