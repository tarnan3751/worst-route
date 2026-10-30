"""`pipeline tiers`: the nodes of each tier, the largest strongly connected part of its roads.

`docs/PLAN.md` §2.6; `docs/reference/pipeline.md`, tiers. Tier `t`'s roads are the edges whose
class is in `config.TIERS[t]`, and its own nodes are the nodes those edges touch. `run_tiers`
keeps, per tier, the largest strongly connected component of that subgraph (networkx; ties go
to the component holding the smallest node id, as in cleanup's connectivity), so a search
restricted to a tier can reach every node it keeps and leave it again. T4 holds every class,
so it keeps the whole cleaned graph. `save` turns the kept nodes into `tier_node_mask` and the
tier's roads between them into `tier_edge_mask`.

It raises `FragmentedTierError` when a tier keeps less than `config.TIER_MIN_RETAINED` of its
own nodes and `allow_fragmented` is not set (a tier with no roads keeps all of nothing).
`save_stage` writes the report, each tier's kept nodes as a sorted list, to `stages/tiers.json`.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

from worst_route.config import TIER_MIN_RETAINED, TIERS
from worst_route.pipeline import layout


@dataclass
class Tier:
    """One tier: its roads, the nodes they touch, and what the largest component keeps."""

    name: str  # "T0".."T4"
    classes: list[str]
    edges: int  # edges of the tier's classes
    nodes: int  # nodes those edges touch
    kept_nodes: int
    kept_edges: int  # the tier's edges with both ends kept
    components: int  # strongly connected components of the tier's roads
    retained: float  # kept_nodes / nodes, 1.0 when the tier has no nodes
    node_ids: list[int] = field(default_factory=list)  # the kept nodes, ascending


@dataclass
class TiersReport:
    """Every tier, and the seconds the step took."""

    tiers: list[Tier] = field(default_factory=list)
    seconds: float = 0.0


class FragmentedTierError(Exception):
    """A tier keeps too few of its own nodes."""

    def __init__(self, report: TiersReport) -> None:
        failed = "; ".join(
            f"{t.name} keeps {t.kept_nodes} of {t.nodes} nodes ({t.retained:.2%})"
            for t in report.tiers
            if t.retained < TIER_MIN_RETAINED
        )
        super().__init__(
            f"{failed}, below TIER_MIN_RETAINED ({TIER_MIN_RETAINED:.0%}); "
            "pass --allow-fragmented to keep it anyway"
        )
        self.report = report


def tier_classes(t: int) -> list[str]:
    """The road classes of tier `t`, as the graph's `highway` strings, sorted."""
    return sorted(c.name.lower() for c in TIERS[t])


def parameters() -> dict[str, Any]:
    """The config values the tiers depend on, JSON-ready."""
    return {
        "TIERS": [tier_classes(t) for t in range(len(TIERS))],
        "TIER_MIN_RETAINED": TIER_MIN_RETAINED,
    }


def run_tiers(graph: nx.MultiDiGraph, *, allow_fragmented: bool = False) -> TiersReport:
    """Each tier's largest strongly connected component of its roads."""
    clock = time.perf_counter()
    report = TiersReport()
    for t in range(len(TIERS)):
        classes = tier_classes(t)
        roads = nx.DiGraph()
        edges = [(u, v) for u, v, c in graph.edges(data="highway") if c in classes]
        roads.add_edges_from(edges)
        components = sorted(
            nx.strongly_connected_components(roads), key=lambda c: (-len(c), min(c))
        )
        keep: set[int] = components[0] if components else set()
        nodes = roads.number_of_nodes()
        report.tiers.append(
            Tier(
                name=f"T{t}",
                classes=classes,
                edges=len(edges),
                nodes=nodes,
                kept_nodes=len(keep),
                kept_edges=sum(1 for u, v in edges if u in keep and v in keep),
                components=len(components),
                retained=len(keep) / nodes if nodes else 1.0,
                node_ids=sorted(keep),
            )
        )
    report.seconds = round(time.perf_counter() - clock, 3)
    if not allow_fragmented and any(t.retained < TIER_MIN_RETAINED for t in report.tiers):
        raise FragmentedTierError(report)
    return report


def save_stage(report: TiersReport, build_dir: Path) -> None:
    """Write the report, with each tier's kept nodes, to `stages/tiers.json`."""
    path = layout.stage_file(build_dir, "tiers.json")
    path.write_text(json.dumps(asdict(report), indent=1) + "\n")
