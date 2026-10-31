"""Enrich on arrays: what the engine and the modes read per edge and per node.

The arrays engine's enrich (`docs/reference/pipeline.md`, **The arrays engine**) computes what
`enrich.run_enrich` sets on the cleaned graph, from the arrays of `stages/cleanup/`
(`cleaned.load_stage`), each value as networkx's, and the same `EnrichReport`. Edges are the
cleaned graph's in their `(u, v, key)` order, which is the order `run_enrich` visits them in and
the build's edge ids; nodes are 0..n-1. Per edge:

- the speed, its source and the time, per tag set (`enrich.parse_maxspeed`, else the class
  default) and then the edge's length, in the same float operations;
- the identity (`names.identity_of`) and the flags (`enrich.edge_flags`), per tag set;
- the road unit: the edges grouped by their OSM node run either way, each group's first edge in
  key order its unit, as `enrich._units` groups them;
- the bearings (`enrich.bearings`) of the road, the geometry less the stubs cleanup added at a
  merged end (`enrich.road_geometry`'s rule, the part holding the end's OSM node looked up among
  the super-junctions' parts);
- the toll booths and level crossings, the POIs placed as `enrich._place_pois` places them;

and per node the delay of the stop signs and signals among its members, `pass_through` (an in-
and an out-neighbour that are not one node, self-loops left out) and the brand index
(`enrich.brand_index`, which both engines call). `save_stage` writes `stages/enrich/` and
`stages/enrich.json`; `load_stage` reads the stage back onto the cleaned graph.
"""

import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import shapely
from numpy.typing import NDArray

from worst_route.config import NODE_DELAY_S, SPEED_DEFAULT_MPH, HighwayClass
from worst_route.pipeline import layout
from worst_route.pipeline.arrays import cleaned
from worst_route.pipeline.arrays.stage import read_stage, write_stage
from worst_route.pipeline.arrays.stage_a import CLUSTER, PLAIN
from worst_route.pipeline.arrays.stage_b import StageBGraph
from worst_route.pipeline.enrich import (
    _FLAG_NAMES,
    COLLECTIBLES,
    KMH_PER_MPH,
    BrandIndex,
    EnrichReport,
    bearings,
    brand_index,
    edge_flags,
    parse_maxspeed,
)
from worst_route.pipeline.names import identity_of

STAGE = "enrich"  # the stage's directory under `stages/`


@dataclass(frozen=True)
class EnrichedGraph:
    """The cleaned graph with what enrich sets: m edges in key order, n nodes."""

    graph: StageBGraph
    edge_speed_kmh: NDArray[np.float64]  # [m]
    edge_speed_parsed: NDArray[np.bool_]  # [m] from `maxspeed`, not the class default
    edge_time_s: NDArray[np.float64]  # [m]
    edge_identity: NDArray[np.int32]  # [m] index into `identities`, -1 for none
    edge_unit: NDArray[np.int64]  # [m] the edge id of its road unit
    edge_flags: NDArray[np.uint8]  # [m] `graph.data.EdgeFlag` bits
    edge_toll_booths: NDArray[np.int64]  # [m]
    edge_level_crossings: NDArray[np.int64]  # [m]
    edge_bearing_in: NDArray[np.int64]  # [m] degrees clockwise from north, 0-359
    edge_bearing_out: NDArray[np.int64]  # [m]
    node_delay_s: NDArray[np.float64]  # [n]
    node_pass_through: NDArray[np.bool_]  # [n]
    identities: list[str]  # ascending
    brands: BrandIndex


def run_enrich(graph: StageBGraph, pois: pa.Table) -> tuple[EnrichedGraph, EnrichReport]:
    """The cleaned graph enriched (`pois` as `filter` wrote them), and what was set."""
    clock = time.perf_counter()
    a = graph.stage_a
    n, m = len(graph.node_x), len(graph.edge_u)
    report = EnrichReport(nodes=n, edges=m)
    tagset = a.edge_tags[graph.edge_from]
    tags = [{"highway": c, **dict(t)} for c, t in a.tagsets]
    classes = np.array([c for c, _ in a.tagsets], dtype=object)[tagset]

    maxspeed = [parse_maxspeed(t["maxspeed"]) if "maxspeed" in t else None for t in tags]
    default = [SPEED_DEFAULT_MPH[HighwayClass[t["highway"].upper()]] * KMH_PER_MPH for t in tags]
    speed = np.array([d if s is None else s for s, d in zip(maxspeed, default, strict=True)])
    speed = speed[tagset]
    parsed = np.array([s is not None for s in maxspeed], dtype=np.bool_)[tagset]
    time_s = graph.edge_length / (speed / 3.6)
    report.speed_parsed = dict(sorted(Counter(classes[parsed].tolist()).items()))
    report.speed_default = dict(sorted(Counter(classes[~parsed].tolist()).items()))

    names = [identity_of(t) for t in tags]
    identities = sorted({s for t in np.unique(tagset).tolist() if (s := names[t]) is not None})
    number = {s: i for i, s in enumerate(identities)}
    of_tagset = np.array([-1 if s is None else number.get(s, -1) for s in names], dtype=np.int32)
    identity = of_tagset[tagset]
    report.identities = len(identities)
    report.edges_with_identity = int(np.count_nonzero(identity >= 0))
    flags = np.array([edge_flags(t) for t in tags], dtype=np.uint8)[tagset]
    report.flags = {name: int(np.count_nonzero(flags & bit)) for name, bit in _FLAG_NAMES.items()}

    run_indptr, run_ids = _runs(graph)
    unit, report.units, report.two_way_pairs = _units(run_indptr, run_ids)
    bearing_in, bearing_out = _bearings(graph, run_indptr, run_ids)
    toll_booths, level_crossings, delay = _place_pois(graph, run_indptr, run_ids, pois, report)
    pass_through = _pass_through(graph)
    report.pass_through = int(np.count_nonzero(pass_through))
    xy = np.column_stack([graph.node_x, graph.node_y])
    brands, report.brands = brand_index(xy, pass_through, f"EPSG:{a.crs_epsg}", pois)
    enriched = EnrichedGraph(
        graph=graph,
        edge_speed_kmh=speed,
        edge_speed_parsed=parsed,
        edge_time_s=time_s,
        edge_identity=identity,
        edge_unit=unit,
        edge_flags=flags,
        edge_toll_booths=toll_booths,
        edge_level_crossings=level_crossings,
        edge_bearing_in=bearing_in,
        edge_bearing_out=bearing_out,
        node_delay_s=delay,
        node_pass_through=pass_through,
        identities=identities,
        brands=brands,
    )
    report.seconds = round(time.perf_counter() - clock, 3)
    return enriched, report


def _runs(graph: StageBGraph) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Each edge's OSM nodes (its stage A edge's), as a CSR table."""
    a = graph.stage_a
    counts = np.diff(a.osmnode_indptr)[graph.edge_from]
    indptr = np.zeros(len(counts) + 1, dtype=np.int64)
    np.cumsum(counts, out=indptr[1:])
    at = np.repeat(a.osmnode_indptr[graph.edge_from] - indptr[:-1], counts) + np.arange(indptr[-1])
    return indptr, a.osmnode_id[at]


def _units(indptr: NDArray[np.int64], ids: NDArray[np.int64]) -> tuple[NDArray[np.int64], int, int]:
    """Each edge's unit (`enrich._units`): the first edge, in key order, over the edge's OSM node
    run either way; and the number of units and of those that run both ways."""
    m = len(indptr) - 1
    first: dict[tuple[int, ...], int] = {}
    directions: dict[tuple[int, ...], set[bool]] = defaultdict(set)
    unit = np.empty(m, dtype=np.int64)
    for e, nodes in enumerate(np.split(ids, indptr[1:-1]) if m else []):
        along = tuple(nodes.tolist())
        run = min(along, along[::-1])  # osm_nodes never repeat an id, so the two differ
        unit[e] = first.setdefault(run, e)  # edges ascending, so a run's first is its smallest
        directions[run].add(along == run)
    return unit, len(first), sum(len(both) == 2 for both in directions.values())


def _bearings(
    graph: StageBGraph, indptr: NDArray[np.int64], ids: NDArray[np.int64]
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Each edge's (in, out) bearings over its road (`enrich.road_geometry`)."""
    m = len(graph.edge_u)
    if not m:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    sizes = np.diff(graph.part_member_indptr)
    order = np.argsort(graph.part_member_id, kind="stable")
    members, member_size = graph.part_member_id[order], np.repeat(sizes, sizes)[order]

    def stubs(nodes: NDArray[np.int32], ends: NDArray[np.int64]) -> NDArray[np.int64]:
        kind = graph.node_kind[nodes]
        count: NDArray[np.int64] = (kind != PLAIN).astype(np.int64)  # a merged node's position
        merged = (kind != PLAIN) & (kind != CLUSTER)
        size = member_size[np.searchsorted(members, ends[merged])]
        count[merged] += size > 1  # and the position of the stage A cluster it took in
        return count

    start = stubs(graph.edge_u, ids[indptr[:-1]])
    end = stubs(graph.edge_v, ids[indptr[1:] - 1])
    counts = np.diff(graph.coord_indptr)
    whole = np.asarray(
        shapely.linestrings(graph.coord_x, graph.coord_y, indices=np.repeat(np.arange(m), counts))
    )
    kept = counts - start - end
    fits = np.flatnonzero(kept >= 2)
    at = np.repeat(
        graph.coord_indptr[fits] + start[fits] - (np.cumsum(kept[fits]) - kept[fits]), kept[fits]
    )
    at = at + np.arange(kept[fits].sum())
    roads = np.asarray(
        shapely.linestrings(
            graph.coord_x[at],
            graph.coord_y[at],
            indices=np.repeat(np.arange(len(fits)), kept[fits]),
        )
    ).reshape(-1)
    lines = whole.copy()
    real = shapely.length(roads) > 0
    lines[fits[real]] = roads[real]
    return bearings(lines.tolist())


def _place_pois(
    graph: StageBGraph,
    indptr: NDArray[np.int64],
    ids: NDArray[np.int64],
    pois: pa.Table,
    report: EnrichReport,
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.float64]]:
    """The collectibles on each edge and each node's delay, as `enrich._place_pois` sets them."""
    n, m = len(graph.node_x), len(graph.edge_u)
    member_node = np.repeat(np.arange(n), np.diff(graph.member_indptr))
    by_member = np.argsort(graph.member_id, kind="stable")
    members, member_node = graph.member_id[by_member], member_node[by_member]
    lengths = np.diff(indptr)
    place = np.arange(len(ids)) - np.repeat(indptr[:-1], lengths)  # within the edge's run
    interior = (place > 0) & (place < np.repeat(lengths, lengths) - 1)
    edge_of = np.repeat(np.arange(m), lengths)[interior]
    inside = ids[interior]
    by_inside = np.argsort(inside, kind="stable")  # an OSM node's edges in key order
    inside, edge_of = inside[by_inside], edge_of[by_inside]
    out_ptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(graph.edge_u, minlength=n), out=out_ptr[1:])

    def node_of(osm_id: int) -> int | None:
        at = int(np.searchsorted(members, osm_id))
        return int(member_node[at]) if at < len(members) and members[at] == osm_id else None

    def edges_holding(osm_id: int) -> list[int]:
        lo, hi = np.searchsorted(inside, osm_id), np.searchsorted(inside, osm_id, side="right")
        held: list[int] = edge_of[lo:hi].tolist()
        return held

    delay = np.zeros(n, dtype=np.float64)
    on_edges = {kind: np.zeros(m, dtype=np.int64) for kind in COLLECTIBLES}
    counts = {kind: Counter[str]() for kind in (*COLLECTIBLES, *NODE_DELAY_S)}
    for row in pois.select(["osm_type", "osm_id", "kind"]).to_pylist():
        kind, osm_id = row["kind"], row["osm_id"]
        if row["osm_type"] != "n" or kind not in counts:
            continue
        counts[kind]["found"] += 1
        node = node_of(osm_id)
        if kind in NODE_DELAY_S:
            if node is not None:
                delay[node] = max(delay[node], NODE_DELAY_S[kind])
                counts[kind]["at_junctions"] += 1
            else:
                counts[kind]["shape_points" if edges_holding(osm_id) else "off_graph"] += 1
            continue
        if node is not None and out_ptr[node] < out_ptr[node + 1]:
            edges = [int(out_ptr[node])]  # its lowest-key out-edge
            counts[kind]["at_junctions"] += 1
        elif held := edges_holding(osm_id):
            edges = held
            counts[kind]["on_edges"] += 1
        else:
            counts[kind]["off_graph"] += 1
            continue
        np.add.at(on_edges[kind], edges, 1)
    for kind in COLLECTIBLES:
        found = counts[kind]
        report.collectibles[kind] = {
            name: found[name] for name in ("found", "on_edges", "at_junctions", "off_graph")
        }
        report.collectibles[kind]["edges"] = int(np.count_nonzero(on_edges[kind]))
    for kind in NODE_DELAY_S:
        found = counts[kind]
        report.delays[kind] = {
            name: found[name] for name in ("found", "at_junctions", "shape_points", "off_graph")
        }
    report.delay_nodes = int(np.count_nonzero(delay > 0))
    return on_edges["toll_booth"], on_edges["level_crossing"], delay


def _pass_through(graph: StageBGraph) -> NDArray[np.bool_]:
    """Whether each node has an in-neighbour and an out-neighbour, not both one node, self-loops
    left out (`enrich.run_enrich`'s rule)."""
    n = len(graph.node_x)
    u, v = graph.edge_u.astype(np.int64), graph.edge_v.astype(np.int64)
    links = np.unique((u * n + v)[u != v])  # distinct (u, v) pairs
    tails, heads = links // n, links % n
    before = np.bincount(heads, minlength=n)  # distinct in-neighbours
    after = np.bincount(tails, minlength=n)
    back = np.isin(heads * n + tails, links)  # (v, u) is a link too
    both = np.bincount(tails[back], minlength=n)  # neighbours both before and after
    return (before > 0) & (after > 0) & (before + after - both >= 2)


def save_stage(graph: EnrichedGraph, report: EnrichReport, build_dir: Path) -> None:
    """Write what enrich set as the `stages/enrich/` stage and the report to
    `stages/enrich.json`. The cleaned graph stays in `stages/cleanup/`, whose manifest's md5 the
    stage's tables record, so the stage changes with the cleaned graph, as `enrich.pkl` does."""
    arrays = {
        f.name: getattr(graph, f.name)
        for f in fields(graph)
        if f.name not in ("graph", "identities", "brands")
    }
    cleanup = layout.stage_file(build_dir, cleaned.STAGE) / layout.STAGE_MANIFEST
    tables = {"identities": graph.identities, "brands": graph.brands}
    tables["cleanup"] = layout.file_md5(cleanup)  # so the manifest changes with the graph
    write_stage(layout.stage_file(build_dir, STAGE), arrays, tables)
    summary = layout.stage_file(build_dir, "enrich.json")
    summary.write_text(json.dumps(asdict(report), indent=2) + "\n")


def load_stage(graph: StageBGraph, build_dir: Path) -> EnrichedGraph:
    """The `stages/enrich/` stage on the cleaned graph it enriched, its arrays memory-mapped."""
    arrays, tables = read_stage(layout.stage_file(build_dir, STAGE))
    brands: list[Any] = tables["brands"]
    return EnrichedGraph(
        graph=graph,
        identities=list(tables["identities"]),
        brands=[(name, wikidata, [(v, k) for v, k in at]) for name, wikidata, at in brands],
        **arrays,
    )
