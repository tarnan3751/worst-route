"""Cleanup, step 5: keep the largest strongly connected component (`docs/PLAN.md` §2.4f).

A node outside it cannot be both reached and left, so no route through it exists.
`largest_component` finds it with networkx (ties go to the component holding the smallest
node id) and reports what the rest would drop: nodes, edges, length (a two-way road counted
once: edges with `reversed` set are left out of the sums) and the `DROPPED_LISTED` largest
dropped components (nodes, length of the edges leaving them, centroid). A large dropped
component is a filtering bug, not noise. Cleanup fails when the kept share of nodes is below
`config.SCC_MIN_RETAINED`, or when the largest dropped component holds more than
`config.SCC_MAX_DROPPED_NODES`, unless told to accept a fragmented graph. Cleanup then drops the
super-junctions connectivity leaves with no allowed turn, and runs it again, adding each round
into the report (`dropped_no_turn`; `cleanup.py`).
"""

from dataclasses import dataclass, field

import networkx as nx
import numpy as np

DROPPED_LISTED = 20


@dataclass
class DroppedComponent:
    """A strongly connected component that connectivity drops."""

    nodes: int
    length_m: float
    lon: float
    lat: float


@dataclass
class ConnectivityReport:
    """What keeping the largest strongly connected component keeps and drops."""

    nodes_before: int = 0
    edges_before: int = 0
    nodes_after: int = 0
    edges_after: int = 0
    dropped_nodes: int = 0
    dropped_edges: int = 0
    dropped_length_m: float = 0.0
    dropped_components: int = 0
    dropped_no_turn: int = 0  # super-junctions cleanup dropped after it, allowing no turn
    retained: float = 1.0  # nodes_after / nodes_before
    largest_dropped: list[DroppedComponent] = field(default_factory=list)


def largest_component(graph: nx.MultiDiGraph) -> tuple[set[int], ConnectivityReport]:
    """The nodes of the largest strongly connected component, and what the others hold."""
    report = ConnectivityReport(graph.number_of_nodes(), graph.number_of_edges())
    components = sorted(nx.strongly_connected_components(graph), key=lambda c: (-len(c), min(c)))
    keep: set[int] = components[0] if components else set()
    dropped = []
    for component in components[1:]:
        nodes = sorted(component)  # float sums in a fixed order
        length = sum(
            data["length"]
            for node in nodes
            for _, _, data in graph.out_edges(node, data=True)
            if not data["reversed"]
        )
        lon = np.mean([graph.nodes[n]["lon"] for n in nodes])
        lat = np.mean([graph.nodes[n]["lat"] for n in nodes])
        dropped.append(DroppedComponent(len(component), float(length), float(lon), float(lat)))
    dropped.sort(key=lambda c: (-c.nodes, -c.length_m))
    lost = [data for u, v, data in graph.edges(data=True) if u not in keep or v not in keep]
    report.nodes_after = len(keep)
    report.edges_after = report.edges_before - len(lost)
    report.dropped_nodes = report.nodes_before - report.nodes_after
    report.dropped_edges = len(lost)
    report.dropped_length_m = float(sum(d["length"] for d in lost if not d["reversed"]))
    report.dropped_components = len(dropped)
    if report.nodes_before:
        report.retained = report.nodes_after / report.nodes_before
    report.largest_dropped = dropped[:DROPPED_LISTED]
    return keep, report
