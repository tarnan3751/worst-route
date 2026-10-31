# The cleanup stage of Portland's latest build, made by either engine, read through
# WORST_ROUTE_DATA. Run on demand with `uv run pytest -m data`; skipped when that build or its
# cleanup stage is not there.
import json
import pickle
from typing import Any

import networkx as nx
import pyarrow.parquet as pq
import pytest

from tests.pipeline.invariants import check_all, lost_through
from tests.pipeline.parity import raw_graph
from worst_route.config import SCC_MAX_DROPPED_NODES, SCC_MIN_RETAINED
from worst_route.pipeline import layout
from worst_route.pipeline.arrays import cleaned
from worst_route.pipeline.arrays.build import load_stage
from worst_route.pipeline.arrays.to_nx import cleanup_graph

pytestmark = pytest.mark.data

type Stages = tuple[nx.MultiDiGraph, nx.MultiDiGraph, dict[str, Any]]


@pytest.fixture(scope="module")
def stages() -> Stages:
    latest = layout.latest_file("portland")
    if not latest.exists():
        pytest.skip(f"no Portland build under {latest.parent}")
    directory = layout.build_dir("portland", latest.read_text().strip())
    report = layout.stage_file(directory, "cleanup.json")
    engine = layout.cleanup_engine(directory)
    if engine is None or not report.exists():
        pytest.skip(f"no cleanup stage in {directory}")
    if engine == "arrays":  # its stages, as networkx holds the graphs
        raw = raw_graph(load_stage(directory))
        return raw, cleanup_graph(cleaned.load_stage(directory)), json.loads(report.read_text())
    # build.pkl, or the arrays engine's build/ where it handed its cleaned graph to a pickle,
    # as it did before its stages after cleanup were its own
    built = layout.build_stage(directory)
    if built.is_dir():
        raw = raw_graph(load_stage(directory))
    else:
        with built.open("rb") as f:
            raw = pickle.load(f)
    with layout.stage_file(directory, "cleanup.pkl").open("rb") as f:
        graph = pickle.load(f)
    return raw, graph, json.loads(report.read_text())


def restriction_rows() -> list[dict[str, object]]:
    """Portland's turn restrictions, none before the filter wrote them."""
    table = layout.restrictions_parquet("portland")
    return pq.read_table(table).to_pylist() if table.exists() else []


def test_portland_invariants(stages: Stages) -> None:
    raw, graph, _ = stages
    check_all(raw, graph, complete=False, restrictions=restriction_rows())
    assert list(graph.nodes) == list(range(graph.number_of_nodes()))


def test_portland_report(stages: Stages) -> None:
    _, graph, report = stages
    assert report["twins"]["coverage"] > 0.95
    assert report["connectivity"]["retained"] >= SCC_MIN_RETAINED
    assert report["connectivity"]["largest_dropped"][0]["nodes"] <= SCC_MAX_DROPPED_NODES
    assert report["connectivity"]["nodes_after"] == graph.number_of_nodes()
    # none in a report before it; since pipeline version 8, the junction of two one-way access
    # roads (ways 566767462 and 566767446, at 45.5388, -122.9138) whose one turn relation
    # 14604969 bans, the third road there being one the filter drops
    assert report["connectivity"].get("dropped_no_turn", 0) <= 1
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
    } | (
        {
            # since pipeline version 8: relation 1621276, only right from N Randolph Ave at
            # 40595800, which bans going on along it there
            ("unclassified", "North Randolph Avenue"),
            # and relation 1706307, a no_u_turn from ramp 1052597840 onto ramp 5512006 at
            # 40403015: their chain from eastbound I 84 at 1894362318 back to it at 3429238808 is
            # a super-junction whose mainline between (424 m and 926 m) lies outside it, longer
            # than a pocket, so the pass through it was by the ramps, and the restriction bans it
            ("motorway", "I 84;US 30"),
        }
        if int(graph.graph["pipeline_version"]) >= 8
        else set()
    )


def test_portland_turn_restrictions(stages: Stages) -> None:
    _, graph, report = stages
    if int(graph.graph["pipeline_version"]) < 8:
        pytest.skip("built before the turn restrictions (pipeline version 8)")
    counts = report["restrictions"]
    skipped = counts["unmatched"] + counts["via_inside_edge"] + counts["via_not_found"]
    assert counts["applied"] + skipped == counts["read"] > 3000
    assert counts["applied"] > 0.9 * counts["read"] and counts["via_not_found"] == 0
    assert 1000 < len(graph.graph["restricted"]) <= counts["junctions"]
