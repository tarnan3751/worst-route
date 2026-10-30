from pathlib import Path

import networkx as nx
import numpy as np
import pytest

from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.connectivity import ConnectivityReport, largest_component
from worst_route.pipeline.consolidate import stage_a, stage_b
from worst_route.pipeline.simplify import simplify

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
UTM_10N = 32610


def consolidated(*names: str) -> nx.MultiDiGraph:
    """The fixtures' raw graphs side by side (their node ids do not overlap), consolidated."""
    graphs = [build_raw_graph(FIXTURES / f"{name}.osm", UTM_10N)[0] for name in names]
    raw = nx.union_all(graphs)
    raw.graph = graphs[0].graph
    return stage_b(stage_a(simplify(raw)[0])[0])[0]


def test_a_disconnected_piece_is_dropped() -> None:
    graph = consolidated("grid", "culdesac")  # grid ids are 1000+, cul-de-sac ids below 100
    keep, report = largest_component(graph)
    assert len(keep) == 36 and all(graph.nodes[n]["members"][0] >= 1000 for n in keep)
    piece = [n for n in graph if n not in keep]
    assert sorted(graph.nodes[n]["members"][0] for n in piece) == [1, 3, 5, 7, 21, 22, 23]
    lost = [d for u, v, d in graph.edges(data=True) if u in piece]
    length = sum(d["length"] for d in lost if not d["reversed"])
    assert report == ConnectivityReport(
        nodes_before=43,
        edges_before=122,
        nodes_after=36,
        edges_after=110,
        dropped_nodes=7,
        dropped_edges=12,
        dropped_length_m=pytest.approx(length),
        dropped_components=1,
        retained=pytest.approx(36 / 43),
        largest_dropped=report.largest_dropped,
    )
    (component,) = report.largest_dropped
    assert (component.nodes, component.length_m) == (7, pytest.approx(length))
    lon = np.mean([graph.nodes[n]["lon"] for n in piece])
    assert (component.lon, component.lat) == pytest.approx(
        (lon, np.mean([graph.nodes[n]["lat"] for n in piece]))
    )


def test_one_way_pieces_are_dropped() -> None:
    graph = consolidated("oneway")  # a motorway stub, a roundabout, one-way streets
    keep, report = largest_component(graph)
    # The roundabout (a super-node now) with its two two-way approaches is the largest piece.
    assert sorted(graph.nodes[n]["kind"] for n in keep) == ["plain", "plain", "roundabout"]
    assert (report.nodes_before, report.nodes_after, report.dropped_components) == (13, 3, 7)
    assert [c.nodes for c in report.largest_dropped] == [2, 2, 2, 1, 1, 1, 1]
    assert report.retained == pytest.approx(3 / 13)


def test_a_connected_graph_keeps_everything() -> None:
    graph = consolidated("grid")
    keep, report = largest_component(graph)
    assert keep == set(graph)
    assert (report.dropped_nodes, report.dropped_edges, report.retained) == (0, 0, 1.0)
    assert report.largest_dropped == []
