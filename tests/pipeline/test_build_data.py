# The saved build of Portland's latest build id, read through WORST_ROUTE_DATA. Run on demand with
# `uv run pytest -m data`; skipped when that build has not been saved.
import json
import pickle
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from tests.pipeline.invariants import check_build, check_saved
from worst_route.config import BRAND_MIN_LOCATIONS, TIERS
from worst_route.graph.data import GraphData
from worst_route.graph.geometry import read_geometry
from worst_route.graph.store import graph_version, read_build
from worst_route.pipeline import layout
from worst_route.pipeline.arrays import cleaned
from worst_route.pipeline.arrays.to_nx import cleanup_graph
from worst_route.pipeline.enrich import run_enrich

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
    """The build holds the enriched graph: the networkx engine's enrich stage, or the arrays
    engine's cleaned graph handed to networkx and enriched there."""
    directory, data = build
    if layout.cleanup_engine(directory) == "arrays":
        graph = cleanup_graph(cleaned.load_stage(directory))
        run_enrich(graph, pq.read_table(layout.pois_parquet("portland")))
    else:
        with layout.stage_file(directory, "enrich.pkl").open("rb") as f:
            graph = pickle.load(f)
    check_saved(graph, data, read_geometry(directory, data.m))


def test_portland_tiers(build: Build) -> None:
    directory, data = build
    stage = json.loads(layout.stage_file(directory, "tiers.json").read_text())
    for t, classes in enumerate(TIERS):
        assert np.flatnonzero(data.tier_node_mask[t]).tolist() == stage["tiers"][t]["node_ids"]
        assert not (data.tier_edge_mask[t] & ~np.isin(data.edge_highway, sorted(classes))).any()
    assert data.tier_node_mask[4].all() and data.tier_edge_mask[4].all()


def test_portland_brands(build: Build) -> None:
    _, data = build
    assert len(data.strings.brands) > 100
    assert (np.diff(data.brand_indptr) >= BRAND_MIN_LOCATIONS).all()
    assert data.node_pass_through[data.brand_nodes].all()
    strings = data.strings
    keys = [(n, w or "") for n, w in zip(strings.brands, strings.brand_wikidata, strict=True)]
    assert keys == sorted(keys)  # the ids are the (name, wikidata) order


def test_portland_qa_passes(build: Build) -> None:
    directory, _ = build
    report = layout.qa_file(directory, "qa.json")
    if not report.exists():
        pytest.skip(f"no QA report in {directory}")
    qa = json.loads(report.read_text())
    assert [t["name"] for t in qa["thresholds"] if not t["passed"]] == []
    assert qa["passed"] is True
