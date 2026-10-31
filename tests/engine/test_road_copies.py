"""A road mapped more than once is one road unit, so no route drives it out on one copy and back
on another: a two-edge round trip, the only way a vertex-simple route can drive a road twice,
which `Route.new`, `Route.splice` and the validator's `edge_simple` refuse by unit. The builds
come from OSM through the pipeline's test helpers (`tests/pipeline/copies.py`'s copies of Mill
Rd), cleaned, enriched and turned into `GraphData` as `pipeline save` does.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from tests.pipeline.chain import cleaned, node_of, poi_table, tier_nodes
from tests.pipeline.copies import COPIES, META, mapped_copies
from worst_route.engine.route import EdgeBanned, Metrics, Route
from worst_route.engine.validator import validate_route
from worst_route.graph.data import GraphData
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.save import graph_data

NO_METRICS = Metrics(length_m=0.0, time_s=0.0)


@dataclass
class Drawn:
    """A route as the validator reads it, legal or not."""

    nodes: np.ndarray
    edges: np.ndarray
    stop_idx: tuple[int, ...]
    metrics: Metrics


def between(data: GraphData, u: int, v: int) -> list[int]:
    """Every edge from u to v."""
    return np.flatnonzero((data.edge_u == u) & (data.edge_v == v)).tolist()


@pytest.mark.parametrize("inner", [False, True], ids=["one segment", "an inner junction"])
@pytest.mark.parametrize("copies", COPIES.values(), ids=COPIES.keys())
def test_out_on_one_copy_and_back_on_another_is_refused(
    tmp_path: Path, copies: list[tuple[bool, bool]], inner: bool
) -> None:
    graph = cleaned(mapped_copies(tmp_path / "copies.osm", copies, inner=inner))
    run_enrich(graph, poi_table([]))
    data = graph_data(graph, tier_nodes(graph), META)
    a, b = node_of(graph, 1), node_of(graph, 2 if inner else 3)
    out, back = between(data, a, b), between(data, b, a)
    assert len(out) * len(back) >= 2  # two copies at least: two ways out and back
    # A legal round trip around the block, whose whole interior the splices replace.
    block = [a, *([b] if inner else []), node_of(graph, 3), node_of(graph, 5), node_of(graph, 4), a]
    block_edges = [between(data, u, v)[0] for u, v in zip(block[:-1], block[1:], strict=True)]
    route = Route.new(
        data,
        np.array(block, dtype=np.int32),
        np.array(block_edges, dtype=np.int32),
        (0, len(block) - 1),
        score=0.0,
        metrics=NO_METRICS,
        seed=0,
    )
    nodes = np.array([a, b, a], dtype=np.int32)
    for e in out:
        for f in back:
            edges = np.array([e, f], dtype=np.int32)
            with pytest.raises(EdgeBanned, match="out and back"):
                Route.new(data, nodes, edges, (0, 2), score=0.0, metrics=NO_METRICS, seed=0)
            with pytest.raises(EdgeBanned, match="out and back"):
                route.splice(data, 0, len(block) - 1, nodes, edges, score=0.0, metrics=NO_METRICS)
            length = float(data.edge_length_m[edges].astype(np.float64).sum())
            time = float(data.edge_time_s[edges].astype(np.float64).sum())
            report = validate_route(
                data,
                Drawn(nodes, edges, (0, 2), Metrics(length, time)),
                stop_nodes=[a, a],
                node_mask=np.ones(data.n, dtype=bool),
                banned_edges=np.zeros(data.m, dtype=bool),
            )
            assert [c.name for c in report.checks if not c.ok] == ["edge_simple"]
            assert "road unit" in report.check("edge_simple").detail
