"""Small named street graphs for the directions tests (`make_graph`, metres from GRID_ORIGIN).

`make_graph` makes every node plain, so `with_kind` gives a node the kind of a stage B
super-junction, as `tests/engine/graphs.py` gives the lattice its delays.
"""

import math
from dataclasses import replace

from worst_route.config import HighwayClass
from worst_route.graph.data import GraphData, NodeKind
from worst_route.graph.store import graph_version
from worst_route.graph.testing import GRID_ORIGIN, Edge, make_graph

X0, Y0 = GRID_ORIGIN
ROWS = ("Southeast Main Street", "Southeast Oak Street", "Southeast Pine Street")
AVENUES = (
    "Southeast 1st Avenue",
    "Southeast 2nd Avenue",
    "Southeast 3rd Avenue",
    "Southeast 4th Avenue",
)


def two_way(u: int, v: int, name: str) -> list[Edge]:
    return [Edge(u, v, name=name), Edge(v, u, name=name)]


def town() -> GraphData:
    """Three streets east-west (ROWS, south to north) and four avenues north-south (AVENUES),
    100 m apart, all two-way: node r * 4 + c at (100 c, 100 r)."""
    nodes = [(X0 + 100.0 * c, Y0 + 100.0 * r) for r in range(3) for c in range(4)]
    edges: list[Edge] = []
    for r in range(3):
        for c in range(3):
            edges += two_way(r * 4 + c, r * 4 + c + 1, ROWS[r])
    for c in range(4):
        for r in range(2):
            edges += two_way(r * 4 + c, (r + 1) * 4 + c, AVENUES[c])
    return make_graph(nodes, edges)


def star(angles: list[int], *, approach: str = "Approach Road") -> GraphData:
    """A junction at node 0 reached from the south (node 1, along `approach`) and left along
    one-way spokes, spoke k (node k + 2) at `angles[k]` degrees clockwise from north, named
    "Spoke <angle>": the turn from node 1 through 0 to node k + 2 changes the bearing by
    that angle."""
    nodes = [(X0, Y0), (X0, Y0 - 100.0)]
    nodes += [
        (X0 + 100 * math.sin(math.radians(a)), Y0 + 100 * math.cos(math.radians(a))) for a in angles
    ]
    edges = [Edge(1, 0, name=approach)]
    edges += [Edge(0, k + 2, name=f"Spoke {a}") for k, a in enumerate(angles)]
    return make_graph(nodes, edges)


def highway() -> GraphData:
    """Southeast Main Street (node 0 to 1), an unnamed ramp of two links (1 to 2 to 3), and a
    motorway with the refs "I 5;US 30" (3 to 4 to 5), all one-way and nearly straight."""
    nodes = [(X0 + x, Y0 + y) for x, y in ((0, 0), (100, 0), (200, 50), (300, 100), (400, 100))]
    nodes.append((X0 + 500, Y0 + 100))
    link, motorway = HighwayClass.MOTORWAY_LINK, HighwayClass.MOTORWAY
    edges = [
        Edge(0, 1, name="Southeast Main Street"),
        Edge(1, 2, highway=link),
        Edge(2, 3, highway=link),
        Edge(3, 4, highway=motorway, ref="I 5;US 30"),
        Edge(4, 5, highway=motorway, ref="I 5;US 30"),
    ]
    return make_graph(nodes, edges)


def corner() -> GraphData:
    """Bend Road turns left at node 1 (east from node 0, then north to node 2); Other Street
    goes on east from node 1 to node 3; an unnamed road (node 3 to 4, east) and an alley
    (node 4 to 5, north) or another unnamed road (node 4 to 6, south) follow it."""
    nodes = [(X0, Y0), (X0 + 100, Y0), (X0 + 100, Y0 + 100), (X0 + 200, Y0)]
    nodes += [(X0 + 300, Y0), (X0 + 300, Y0 + 100), (X0 + 300, Y0 - 100)]
    edges = [
        Edge(0, 1, name="Bend Road"),
        Edge(1, 2, name="Bend Road"),
        Edge(1, 3, name="Other Street"),
        Edge(3, 4),
        Edge(4, 5, highway=HighwayClass.ALLEY),
        Edge(4, 6),
    ]
    return make_graph(nodes, edges)


def us26() -> GraphData:
    """US 26 through downtown as OSM tags it: Southwest Sheridan Street east from node 0 to 1
    without a ref and on to 2 with the ref US 26 (another identity, the same name), then on
    US 26 right (south) to 3 along Southwest 3rd Avenue, left (east) to 4 along Southwest
    Arthur Street, and left again (north) to 5, still Southwest Arthur Street."""
    nodes = [(X0 + x, Y0 + y) for x, y in ((0, 0), (100, 0), (200, 0), (200, -100), (300, -100))]
    nodes.append((X0 + 300, Y0))
    edges = [
        Edge(0, 1, name="Southwest Sheridan Street"),
        Edge(1, 2, name="Southwest Sheridan Street", ref="US 26"),
        Edge(2, 3, name="Southwest 3rd Avenue", ref="US 26"),
        Edge(3, 4, name="Southwest Arthur Street", ref="US 26"),
        Edge(4, 5, name="Southwest Arthur Street", ref="US 26"),
    ]
    return make_graph(nodes, edges)


def with_kind(g: GraphData, node: int, kind: NodeKind) -> GraphData:
    """`g` with `node` of `kind`."""
    kinds = g.node_kind.copy()
    kinds[node] = kind
    out = replace(g, node_kind=kinds, meta=dict(g.meta))
    out.node_kind.flags.writeable = False
    out.meta["graph_version"] = graph_version(out)
    return out
