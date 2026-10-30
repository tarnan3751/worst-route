# The cleanup stage of Portland's latest build, read through WORST_ROUTE_DATA. Run on demand with
# `uv run pytest -m data`; skipped when that build or its cleanup stage is not there.
import json
import pickle
from typing import Any

import networkx as nx
import pytest

from tests.pipeline.invariants import check_all
from worst_route.config import SCC_MIN_RETAINED
from worst_route.pipeline import layout

pytestmark = pytest.mark.data

type Stages = tuple[nx.MultiDiGraph, nx.MultiDiGraph, dict[str, Any]]


@pytest.fixture(scope="module")
def stages() -> Stages:
    latest = layout.latest_file("portland")
    if not latest.exists():
        pytest.skip(f"no Portland build under {latest.parent}")
    directory = layout.build_dir("portland", latest.read_text().strip())
    paths = [layout.stage_file(directory, name) for name in ("build.pkl", "cleanup.pkl")]
    report = layout.stage_file(directory, "cleanup.json")
    if not all(path.exists() for path in [*paths, report]):
        pytest.skip(f"no cleanup stage in {directory}")
    graphs = []
    for path in paths:
        with path.open("rb") as f:
            graphs.append(pickle.load(f))
    return graphs[0], graphs[1], json.loads(report.read_text())


def test_portland_invariants(stages: Stages) -> None:
    raw, graph, _ = stages
    check_all(raw, graph, complete=False)
    assert list(graph.nodes) == list(range(graph.number_of_nodes()))


def test_portland_report(stages: Stages) -> None:
    _, graph, report = stages
    assert report["twins"]["coverage"] > 0.95
    assert report["connectivity"]["retained"] >= SCC_MIN_RETAINED
    assert report["connectivity"]["nodes_after"] == graph.number_of_nodes()
    made = sum(report["stage_b"]["superjunctions"].values())
    assert 0 < len(graph.graph["superjunctions"]) <= made  # some lie in dropped components
