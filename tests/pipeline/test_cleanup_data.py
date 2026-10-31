# The cleanup stage of Portland's latest build, made by either engine, read through
# WORST_ROUTE_DATA. Run on demand with `uv run pytest -m data`; skipped when that build or its
# cleanup stage is not there.
import json
import pickle
from typing import Any

import networkx as nx
import pytest

from tests.pipeline.invariants import check_all, lost_through
from tests.pipeline.parity import raw_graph
from worst_route.config import SCC_MAX_DROPPED_NODES, SCC_MIN_RETAINED
from worst_route.pipeline import layout
from worst_route.pipeline.arrays.build import load_stage

pytestmark = pytest.mark.data

type Stages = tuple[nx.MultiDiGraph, nx.MultiDiGraph, dict[str, Any]]


@pytest.fixture(scope="module")
def stages() -> Stages:
    latest = layout.latest_file("portland")
    if not latest.exists():
        pytest.skip(f"no Portland build under {latest.parent}")
    directory = layout.build_dir("portland", latest.read_text().strip())
    built, cleaned = layout.build_stage(directory), layout.stage_file(directory, "cleanup.pkl")
    report = layout.stage_file(directory, "cleanup.json")
    if not all(path.exists() for path in (built, cleaned, report)):
        pytest.skip(f"no cleanup stage in {directory}")
    if built.is_dir():  # the arrays engine's build stage, as networkx holds the raw graph
        raw = raw_graph(load_stage(directory))
    else:
        with built.open("rb") as f:
            raw = pickle.load(f)
    with cleaned.open("rb") as f:
        graph = pickle.load(f)
    return raw, graph, json.loads(report.read_text())


def test_portland_invariants(stages: Stages) -> None:
    raw, graph, _ = stages
    check_all(raw, graph, complete=False)
    assert list(graph.nodes) == list(range(graph.number_of_nodes()))


def test_portland_report(stages: Stages) -> None:
    _, graph, report = stages
    assert report["twins"]["coverage"] > 0.95
    assert report["connectivity"]["retained"] >= SCC_MIN_RETAINED
    assert report["connectivity"]["largest_dropped"][0]["nodes"] <= SCC_MAX_DROPPED_NODES
    assert report["connectivity"]["nodes_after"] == graph.number_of_nodes()
    assert report["connectivity"].get("dropped_no_turn", 0) == 0  # none in a report before it
    made = sum(report["stage_b"]["superjunctions"].values())
    assert 0 < len(graph.graph["superjunctions"]) <= made  # some lie in dropped components


def test_portland_roads_pass_their_junctions_once(stages: Stages) -> None:
    _, graph, _ = stages
    if int(graph.graph["pipeline_version"]) < 5:
        pytest.skip("built before stage B's carriageway joins (pipeline version 5)")
    lost = {
        (graph.edges[edge]["highway"], graph.edges[edge].get("ref") or graph.edges[edge]["name"])
        for edge in ((u, v, k) for v, u, k in lost_through(graph))
    }
    # the ones left are other defects, named in docs/project_overview.md §2.3: a pocket longer
    # than STAGE_B_POCKET_M (I-5 and US 30 by the Rose Quarter, 1,497 m), and roads without a ref
    # that leave their junction under one name and come back under another, which no pocket
    # follows (each listed by the name it leaves under)
    assert lost == {
        ("motorway", "I 5;US 30"),
        ("secondary", "North Argyle Way"),  # back as North Columbia Boulevard
        ("secondary", "North Greeley Avenue"),  # back as North Going Street, 756 m
        ("tertiary", "Northeast Cascades Parkway"),  # by Northeast Mount Hood Avenue
        ("residential", "Southeast 91st Avenue"),  # by SE Tolman Street and SE 90th Avenue
        ("trunk", "Northwest Zion Church Road"),  # back as Northwest Glencoe Road
        ("trunk", "South Barlow Road"),  # back as South Arndt Road
    }
