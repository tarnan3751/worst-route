"""`pipeline cleanup`: the raw graph made into the graph the no-repeat rule rests on.

`docs/PLAN.md` §2.4; `docs/reference/pipeline.md`, cleanup. `run_cleanup` takes the raw graph
`build` wrote and runs, in order: `simplify` (self-loops dropped, degree-2 chains collapsed),
`consolidate.stage_a` (signal clusters and divided crossings), `carriageways.find_twins` (on the
stage A graph, so stretches stage B makes internal keep their twins), `consolidate.stage_b`
(interchanges and roundabouts as super-junctions with their transitions, the twins carried
across, then nodes numbered 0..n-1) and `connectivity.largest_component`, which keeps the largest
strongly connected component and numbers the nodes 0..n-1 again. Connectivity does not read the
turn tables, and dropping an edge drops the transitions that name it, so a super-junction can be
left with edges in and out and no allowed pair between them: where a region's edge cuts an
interchange, its pairs ran through the stubs beyond it. No route can pass through such a node,
and a build cannot say so (a node without transition rows allows every turn), so it is dropped
with its edges, connectivity runs again on what is left, and so on until none remains; the
connectivity report adds the rounds up (`dropped_no_turn` counts those super-junctions). It
raises `FragmentedGraphError`, before writing anything, when the kept share of the stage B
graph's nodes is below `config.SCC_MIN_RETAINED`, or a component it drops holds more than
`config.SCC_MAX_DROPPED_NODES`, and `allow_fragmented` is not set. `save_stage` pickles
the graph, with its `superjunctions` and `twins` tables in `G.graph`, to `stages/cleanup.pkl`
and writes the report to `stages/cleanup.json`. `parameters` is what the step's sidecar records
beside the input, so a changed threshold reruns it.
"""

import json
import pickle
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

from worst_route import config
from worst_route.pipeline import layout
from worst_route.pipeline.carriageways import TwinStats, find_twins
from worst_route.pipeline.connectivity import (
    DROPPED_LISTED,
    ConnectivityReport,
    largest_component,
)
from worst_route.pipeline.consolidate import StageAStats, StageBStats, relabel, stage_a, stage_b
from worst_route.pipeline.simplify import SimplifyStats, simplify

PARAMETERS = (
    "STAGE_A_TOLERANCE_ROUNDABOUT_M",
    "STAGE_A_TOLERANCE_ARTERIAL_M",
    "STAGE_A_TOLERANCE_M",
    "STAGE_A_ARTERIAL_CLASSES",
    "STAGE_A_MAX_LENGTH_M",
    "STAGE_B_JOIN_M",
    "STAGE_B_CARRIAGEWAY_CLASSES",
    "STAGE_B_MINOR_LINKS",
    "STAGE_B_SLIP_CENTER_M",
    "STAGE_B_POCKET_M",
    "STAGE_B_MAX_DIAMETER_M",
    "STAGE_B_SPLIT_EPS_M",
    "TWIN_CLASSES",
    "TWIN_GEOMETRY_ONLY_CLASSES",
    "TWIN_LATERAL_M",
    "TWIN_BEARING_TOL",
    "SCC_MIN_RETAINED",
    "SCC_MAX_DROPPED_NODES",
)


@dataclass
class CleanupReport:
    """Counts before and after each step of cleanup, and the seconds each took."""

    input: dict[str, int] = field(default_factory=dict)  # nodes, edges of the raw graph
    simplify: SimplifyStats = field(default_factory=SimplifyStats)
    stage_a: StageAStats = field(default_factory=StageAStats)
    stage_b: StageBStats = field(default_factory=StageBStats)
    twins: TwinStats = field(default_factory=TwinStats)
    connectivity: ConnectivityReport = field(default_factory=ConnectivityReport)
    seconds: dict[str, float] = field(default_factory=dict)


class FragmentedGraphError(Exception):
    """The largest strongly connected component keeps too few of the nodes, or the graph
    would lose a component too large to be noise."""

    def __init__(self, report: ConnectivityReport) -> None:
        largest = "; ".join(
            f"{c.nodes} nodes, {c.length_m:.0f} m at ({c.lon:.5f}, {c.lat:.5f})"
            for c in report.largest_dropped[:3]
        )
        kept = (
            f"the largest strongly connected component keeps {report.nodes_after} of "
            f"{report.nodes_before} nodes ({report.retained:.2%})"
        )
        if report.retained < config.SCC_MIN_RETAINED:
            kept += f", below SCC_MIN_RETAINED ({config.SCC_MIN_RETAINED:.0%})"
        if _largest_dropped(report) > config.SCC_MAX_DROPPED_NODES:
            kept += (
                f", and a component it drops has more than SCC_MAX_DROPPED_NODES "
                f"({config.SCC_MAX_DROPPED_NODES})"
            )
        super().__init__(
            f"{kept}; largest dropped: {largest}; pass --allow-fragmented to keep it anyway"
        )
        self.report = report


def _largest_dropped(report: ConnectivityReport) -> int:
    """The nodes of the largest component connectivity drops, 0 when it drops none."""
    return report.largest_dropped[0].nodes if report.largest_dropped else 0


def parameters() -> dict[str, Any]:
    """The config values cleanup depends on, JSON-ready."""
    values = {}
    for name in PARAMETERS:
        value = getattr(config, name)
        values[name] = sorted(value) if isinstance(value, frozenset) else value
    return values


def run_cleanup(
    raw: nx.MultiDiGraph, *, allow_fragmented: bool = False
) -> tuple[nx.MultiDiGraph, CleanupReport]:
    """The cleaned graph and what each step did."""
    report = CleanupReport(input={"nodes": raw.number_of_nodes(), "edges": raw.number_of_edges()})
    clock = time.perf_counter()

    def lap(step: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        report.seconds[step] = round(now - clock, 3)
        clock = now

    graph, report.simplify = simplify(raw)
    lap("simplify")
    graph, report.stage_a = stage_a(graph)
    lap("stage_a")
    report.twins = find_twins(graph)
    lap("twins")
    graph, report.stage_b = stage_b(graph)
    lap("stage_b")
    keep, report.connectivity = largest_component(graph)
    scc = report.connectivity
    graph = _without_no_turn(relabel(graph, keep), scc)
    if not allow_fragmented and (
        scc.retained < config.SCC_MIN_RETAINED
        or _largest_dropped(scc) > config.SCC_MAX_DROPPED_NODES
    ):
        raise FragmentedGraphError(scc)
    lap("connectivity")
    return graph, report


def _without_no_turn(graph: nx.MultiDiGraph, scc: ConnectivityReport) -> nx.MultiDiGraph:
    """The graph less every super-junction with edges in and out that allows no pair between
    them, and less what connectivity drops after each round of those; each round added to `scc`.
    """
    dropped = list(scc.largest_dropped)
    while no_turn := {
        node
        for node, record in graph.graph["superjunctions"].items()
        if not record["transitions"] and graph.in_degree(node) and graph.out_degree(node)
    }:
        lost = [d for u, v, d in graph.edges(data=True) if u in no_turn or v in no_turn]
        trimmed = relabel(graph, [node for node in graph if node not in no_turn])
        keep, again = largest_component(trimmed)
        scc.dropped_no_turn += len(no_turn)
        scc.dropped_nodes += len(no_turn) + again.dropped_nodes
        scc.dropped_edges += len(lost) + again.dropped_edges
        scc.dropped_length_m += sum(d["length"] for d in lost if not d["reversed"])
        scc.dropped_length_m += again.dropped_length_m
        scc.dropped_components += again.dropped_components
        scc.nodes_after, scc.edges_after = again.nodes_after, again.edges_after
        scc.retained = scc.nodes_after / scc.nodes_before
        dropped.extend(again.largest_dropped)
        dropped.sort(key=lambda c: (-c.nodes, -c.length_m))
        scc.largest_dropped = dropped[:DROPPED_LISTED]
        graph = relabel(trimmed, keep)
    return graph  # untouched when nothing was dropped: networkx caches the views it is asked for


def save_stage(graph: nx.MultiDiGraph, report: CleanupReport, build_dir: Path) -> None:
    """Write the graph to `stages/cleanup.pkl` and the report to `stages/cleanup.json`."""
    pkl = layout.stage_file(build_dir, "cleanup.pkl")
    with pkl.open("wb") as f:
        pickle.dump(graph, f, protocol=pickle.HIGHEST_PROTOCOL)
    summary = layout.stage_file(build_dir, "cleanup.json")
    summary.write_text(json.dumps(asdict(report), indent=2) + "\n")
