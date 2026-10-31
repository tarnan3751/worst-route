"""Exact comparisons of the two engines' outputs, for the parity tests.

`assert_same_graph(a, b)` compares two `MultiDiGraph`s as networkx holds them, which is what a
pickle of each writes: the graph's tables in order, the nodes in iteration order with their
attributes, the edges in iteration order with their keys and attributes, each node's
predecessors in order; every value exactly (floats to the bit, a geometry by its coordinates)
and every dict's keys in their order. `assert_same_build(a, b)` compares two `GraphData` array
by array (dtype and bytes), then the strings and the graph_version. `raw_graph(raw)` is the
networkx raw graph of the arrays engine's `RawGraph`, as `build.build_raw_graph` makes it, and
`read_back(raw)` the networkx raw graph as `pipeline cleanup` reads it back from `build.pkl`.
"""

import pickle
from typing import Any

import networkx as nx
import numpy as np
import shapely

from worst_route.graph.data import ARRAY_SPECS, GraphData
from worst_route.graph.store import graph_version
from worst_route.pipeline import PIPELINE_VERSION
from worst_route.pipeline.arrays.build import RawGraph


def same(a: Any, b: Any) -> bool:
    """Whether two attribute values are the same: a geometry by its coordinates, a dict with its
    keys in the same order, anything else of the same type and equal."""
    if isinstance(a, shapely.Geometry) or isinstance(b, shapely.Geometry):
        return type(a) is type(b) and np.array_equal(
            shapely.get_coordinates(a), shapely.get_coordinates(b)
        )
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return (
            type(a) is type(b)
            and len(a) == len(b)
            and all(same(x, y) for x, y in zip(a, b, strict=True))
        )
    return type(a) is type(b) and bool(a == b)


def assert_same_graph(a: nx.MultiDiGraph, b: nx.MultiDiGraph) -> None:
    """The graphs hold the same tables, nodes, edges and predecessors, in the same order."""
    assert list(a.graph) == list(b.graph), "the graph's tables or their order differ"
    for name in a.graph:
        assert same(a.graph[name], b.graph[name]), f"graph[{name!r}] differs"
    nodes_a, nodes_b = list(a.nodes(data=True)), list(b.nodes(data=True))
    assert [n for n, _ in nodes_a] == [n for n, _ in nodes_b], "the nodes or their order differ"
    for (node, x), (_, y) in zip(nodes_a, nodes_b, strict=True):
        assert same(x, y), f"node {node}: {x} != {y}"
        assert list(a.pred[node]) == list(b.pred[node]), f"node {node}'s predecessors differ"
    edges_a = list(a.edges(keys=True, data=True))
    edges_b = list(b.edges(keys=True, data=True))
    assert len(edges_a) == len(edges_b), "the edge counts differ"
    for i, ((u, v, k, x), (s, t, j, y)) in enumerate(zip(edges_a, edges_b, strict=True)):
        assert (u, v, k) == (s, t, j), f"edge {i}: ({u}, {v}, {k}) != ({s}, {t}, {j})"
        assert same(x, y), f"edge {i} ({u}, {v}, {k}): {x} != {y}"


def assert_same_build(a: GraphData, b: GraphData) -> None:
    """The builds hold the same arrays, byte for byte, strings and graph_version."""
    for name in ARRAY_SPECS:
        x, y = getattr(a, name), getattr(b, name)
        assert x.dtype == y.dtype and x.shape == y.shape, f"{name}: dtype or shape differs"
        assert x.tobytes() == y.tobytes(), f"{name} differs"
    assert a.strings == b.strings
    assert graph_version(a) == graph_version(b)


def read_back(raw: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """The networkx raw graph as `pipeline cleanup` reads it, from `build.pkl`: unpickled, which
    is what the arrays engine's graphs reproduce to the byte (keys read back are one object each,
    apart from the code's own strings)."""
    loaded: nx.MultiDiGraph = pickle.loads(pickle.dumps(raw, pickle.HIGHEST_PROTOCOL))
    return loaded


def raw_graph(raw: RawGraph) -> nx.MultiDiGraph:
    """The networkx raw graph of a `RawGraph`, built as `build.build_raw_graph` builds it."""
    graph = nx.MultiDiGraph(crs=f"EPSG:{raw.crs_epsg}", pipeline_version=PIPELINE_VERSION)
    for k, node in enumerate(raw.node_id.tolist()):
        graph.add_node(
            node,
            x=float(raw.node_x[k]),
            y=float(raw.node_y[k]),
            lon=float(raw.node_lon[k]),
            lat=float(raw.node_lat[k]),
        )
    for k in range(len(raw.seg_u)):
        lo, hi = int(raw.ref_indptr[k]), int(raw.ref_indptr[k + 1])
        osm_nodes = tuple(raw.ref_id[lo:hi].tolist())
        line = shapely.LineString(np.column_stack([raw.ref_x[lo:hi], raw.ref_y[lo:hi]]))
        road_class, tags = raw.tagsets[raw.seg_tags[k]]
        forward, backward = bool(raw.seg_forward[k]), bool(raw.seg_backward[k])
        common = {
            "osmid": int(raw.seg_way[k]),
            "highway": road_class,
            **dict(tags),
            "oneway": forward != backward,
            "length": float(raw.seg_length[k]),
        }
        if forward:
            graph.add_edge(
                osm_nodes[0], osm_nodes[-1], **common, reversed=False, geometry=line,
                osm_nodes=osm_nodes,
            )  # fmt: skip
        if backward:
            graph.add_edge(
                osm_nodes[-1], osm_nodes[0], **common, reversed=forward,
                geometry=shapely.reverse(line), osm_nodes=osm_nodes[::-1],
            )  # fmt: skip
    return graph
