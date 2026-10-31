"""The tiers on arrays: each tier's largest strongly connected part of its roads.

The arrays engine's tiers (`docs/reference/pipeline.md`, **The arrays engine**): what
`tiers.run_tiers` finds on the enriched graph, found on the cleaned graph's arrays. Tier `t`'s
roads are the edges whose class is in `tiers.tier_classes(t)`, its nodes the nodes they touch;
scipy's strongly connected components of those roads (the partition is unique) give the largest,
ties to the one holding the smallest node, as there, and the same `TiersReport`, which
`tiers.save_stage` writes to `stages/tiers.json`.
"""

import time

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from worst_route.config import TIER_MIN_RETAINED, TIERS
from worst_route.pipeline.arrays.stage_b import StageBGraph
from worst_route.pipeline.tiers import FragmentedTierError, Tier, TiersReport, tier_classes


def run_tiers(graph: StageBGraph, *, allow_fragmented: bool = False) -> TiersReport:
    """Each tier's largest strongly connected component of its roads."""
    clock = time.perf_counter()
    report = TiersReport()
    a = graph.stage_a
    classes = np.array([c for c, _ in a.tagsets], dtype=object)[a.edge_tags[graph.edge_from]]
    for t in range(len(TIERS)):
        names = tier_classes(t)
        roads = np.isin(classes, names) if len(classes) else np.zeros(0, dtype=np.bool_)
        u, v = graph.edge_u[roads], graph.edge_v[roads]
        nodes = np.unique(np.concatenate([u, v]))  # the nodes the tier's roads touch, ascending
        keep = np.zeros(0, dtype=np.int64)
        count = 0
        if len(nodes):
            tu, tv = np.searchsorted(nodes, u), np.searchsorted(nodes, v)
            links = coo_matrix((np.ones(len(tu)), (tu, tv)), shape=(len(nodes), len(nodes)))
            count, label = connected_components(links, directed=True, connection="strong")
            size = np.bincount(label, minlength=count)
            smallest = np.full(count, len(nodes), dtype=np.int64)
            np.minimum.at(smallest, label, np.arange(len(nodes)))
            largest = np.lexsort((smallest, -size))[0]  # ties to the smallest node
            keep = nodes[label == largest]
        kept = np.zeros(len(graph.node_x), dtype=np.bool_)
        kept[keep] = True
        report.tiers.append(
            Tier(
                name=f"T{t}",
                classes=names,
                edges=len(u),
                nodes=len(nodes),
                kept_nodes=len(keep),
                kept_edges=int(np.count_nonzero(kept[u] & kept[v])),
                components=int(count),
                retained=len(keep) / len(nodes) if len(nodes) else 1.0,
                node_ids=keep.tolist(),
            )
        )
    report.seconds = round(time.perf_counter() - clock, 3)
    if not allow_fragmented and any(t.retained < TIER_MIN_RETAINED for t in report.tiers):
        raise FragmentedTierError(report)
    return report
