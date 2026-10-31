"""OSM turn restrictions in the engine: the build's rows at the junctions a restriction gave a table
are transitions like a super-junction's, so a planned route never takes a banned turn, the
validator proves it, and a route drawn through one fails its `transitions` check. The build comes
from `tests/fixtures/osm/restrictions.osm` through the pipeline (its header has the network and
the restrictions), cleaned with its restrictions table, enriched and saved as `GraphData`."""

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from tests.pipeline.chain import cleaned, node_of, poi_table, restrictions_of, tier_nodes
from tests.pipeline.copies import META
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.engine.route import Metrics
from worst_route.engine.validator import validate_route
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.save import graph_data

FIXTURE = Path(__file__).parent.parent / "fixtures" / "osm" / "restrictions.osm"


@dataclass(frozen=True)
class Built:
    data: GraphData
    node: dict[int, int]  # OSM node id -> build node


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Built:
    table = restrictions_of(FIXTURE, tmp_path_factory.mktemp("restrictions"))
    graph = cleaned(FIXTURE, restrictions=table)
    run_enrich(graph, poi_table([]))
    data = graph_data(graph, tier_nodes(graph), META)
    return Built(data, {m: node_of(graph, m) for m in (10, 11, 12, 13, 21, 23, 31, 33, 37)})


def edge(data: GraphData, u: int, v: int) -> int:
    (e,) = np.flatnonzero((data.edge_u == u) & (data.edge_v == v)).tolist()
    return int(e)


def banned(data: GraphData) -> set[tuple[int, int]]:
    """Every (in, out) pair a node with rows leaves out."""
    rows = set(zip(data.trans_in.tolist(), data.trans_out.tolist(), strict=True))
    pairs = set()
    for v in set(data.trans_node.tolist()):
        ins = data.in_edges[data.in_indptr[v] : data.in_indptr[v + 1]].tolist()
        outs = data.out_edges[data.out_indptr[v] : data.out_indptr[v + 1]].tolist()
        pairs |= {(a, b) for a in ins for b in outs if (a, b) not in rows}
    return pairs


def test_the_left_turn_at_alder_and_birch_is_banned(built: Built) -> None:
    data, at = built.data, built.node
    left = (edge(data, at[10], at[11]), edge(data, at[11], at[21]))
    straight = (left[0], edge(data, at[11], at[12]))
    assert left in banned(data) and straight not in banned(data)
    assert len(set(data.trans_node.tolist())) == 7  # six restricted junctions, one super-junction


@pytest.mark.parametrize("backend", [PythonBackend, CsrBackend])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_planned_route_takes_no_banned_turn(built: Built, backend: type, seed: int) -> None:
    # From Alder St's west end to Birch Ave's north end, which the shortest way reaches by the
    # left turn at 11 that 9001 bans.
    data, at = built.data, built.node
    stops = [(float(data.node_lat[at[o]]), float(data.node_lon[at[o]])) for o in (10, 21)]
    request = RouteRequest(stops=stops, seed=seed, max_iterations=300, budget_s=60)
    result = plan_route(data, request, backend=backend(data))
    assert result.report.ok, result.report
    assert not set(pairwise(result.route.edges.tolist())) & banned(data)
    # Not so the baseline, a lower bound: it is the turn-blind shortest path where the cheapest
    # legal walk passes a node twice (`docs/reference/engine.md`, **Seed and baseline**), here on
    # to 12 and back along Alder St, so it takes the left turn.


def test_a_route_through_a_banned_turn_fails_the_validator(built: Built) -> None:
    data, at = built.data, built.node

    @dataclass
    class Drawn:
        nodes: np.ndarray
        edges: np.ndarray
        stop_idx: tuple[int, ...]
        metrics: Metrics

    nodes = np.array([at[10], at[11], at[21]], dtype=np.int64)
    edges = np.array([edge(data, at[10], at[11]), edge(data, at[11], at[21])], dtype=np.int64)
    length = float(data.edge_length_m[edges].astype(np.float64).sum())
    time = float(data.edge_time_s[edges].astype(np.float64).sum())
    report = validate_route(
        data,
        Drawn(nodes, edges, (0, 2), Metrics(length, time)),
        stop_nodes=[at[10], at[21]],
        node_mask=np.ones(data.n, dtype=bool),
        banned_edges=np.zeros(data.m, dtype=bool),
    )
    assert [c.name for c in report.checks if not c.ok] == ["transitions"]
    assert f"not allowed at node {at[11]}" in report.check("transitions").detail
