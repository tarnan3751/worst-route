"""`pipeline build --engine arrays`: the filtered roads as the raw graph's arrays.

The arrays engine's `build` (`docs/reference/pipeline.md`, **The arrays engine**). It reads the
roads file once, as `pipeline.build.build_raw_graph` does, and makes the same raw graph as
arrays: the same kept ways (`exclusions.kept_class` and the region's class ceiling again, a way
id seen twice read once, a node repeated back to back read once), the same junctions
(referenced at least twice, or ending a way), the same segments in the same order, the same
one-way rule (`build.direction`), the same coordinates, lengths and stats. Node locations are
read as osmium's fixed-point integers, whose quotient by 10^7 is exactly the `lon` and `lat`
osmium gives; projection is pyproj's, and each segment's length is GEOS's (`shapely.length`), in
chunks of `LENGTH_CHUNK` segments.

`RawGraph` holds the result. Its nodes are the junctions that end a segment, in the order the
networkx graph holds them (first appearance as a segment's first or last node); its segments are
in way order, each driven forward (node order) and backward as its rule allows: segment k's
forward edge then its backward edge is the order in which networkx inserted them, which
`simplify.py` needs. `save_stage` writes it as the `stages/build/` stage (`stage.py`) and the
stats to `stages/build.json`, as the networkx engine does.
"""

import json
from array import array
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, cast

import numpy as np
import osmium
import pyproj
import shapely
from numpy.typing import NDArray

from worst_route.pipeline import layout
from worst_route.pipeline.arrays.stage import read_stage, write_stage
from worst_route.pipeline.build import EDGE_TAGS, BuildStats, direction
from worst_route.pipeline.exclusions import RoadClass, kept_class

LENGTH_CHUNK = 1_000_000  # segments per call of shapely.length
FIXED = 10_000_000  # osmium's fixed-point coordinate precision

# Each one-way rule's name, in `build.direction`'s order of checks.
RULES = (
    "oneway",
    "oneway_reverse",
    "roundabout",
    "motorway_implied",
    "oneway_no",
    "reversible",
    "two_way",
)

type Tagset = tuple[str, tuple[tuple[str, str], ...]]  # the kept class, then the EDGE_TAGS present


@dataclass(frozen=True)
class RawGraph:
    """The raw graph as arrays: n nodes, s segments, r refs (each segment's OSM nodes)."""

    crs_epsg: int
    node_id: NDArray[np.int64]  # [n] OSM node id, in networkx's order
    node_x: NDArray[np.float64]  # [n] projected
    node_y: NDArray[np.float64]
    node_lon: NDArray[np.float64]
    node_lat: NDArray[np.float64]
    seg_u: NDArray[np.int32]  # [s] node index of the segment's first node
    seg_v: NDArray[np.int32]  # [s] and of its last
    seg_way: NDArray[np.int64]  # [s] OSM way id
    seg_tags: NDArray[np.int32]  # [s] index into `tagsets`
    seg_forward: NDArray[np.bool_]  # [s] drivable in node order
    seg_backward: NDArray[np.bool_]  # [s] and against it
    seg_length: NDArray[np.float64]  # [s] metres
    ref_indptr: NDArray[np.int64]  # [s + 1] segment k's refs are [ref_indptr[k], ref_indptr[k + 1])
    ref_id: NDArray[np.int64]  # [r] OSM node ids along the segments, in node order
    ref_x: NDArray[np.float64]  # [r] projected
    ref_y: NDArray[np.float64]
    tagsets: list[Tagset]

    @property
    def edges(self) -> int:
        """The number of directed edges."""
        return int(self.seg_forward.sum() + self.seg_backward.sum())


def build_raw_arrays(
    roads: Path, crs_epsg: int, classes: frozenset[RoadClass] | None = None
) -> tuple[RawGraph, BuildStats]:
    """The raw graph of the roads file as arrays, its coordinates projected to `crs_epsg`; with
    `classes`, of the roads of those classes only."""
    stats = BuildStats()
    node_ids, fixed_lon, fixed_lat = array("q"), array("i"), array("i")
    way_ids, way_sizes, way_tags = array("q"), array("q"), array("i")
    way_rules, way_forward, way_backward = array("b"), array("b"), array("b")
    refs = array("q")
    tagsets: dict[Tagset, int] = {}
    for entity in osmium.FileProcessor(roads, osmium.osm.NODE | osmium.osm.WAY):
        if isinstance(entity, osmium.osm.Node):
            location = entity.location
            if not location.valid():  # as reading its lon in `build_raw_graph` raises
                raise osmium.InvalidLocationError("invalid location")
            node_ids.append(entity.id)
            fixed_lon.append(location.x)
            fixed_lat.append(location.y)
            continue
        osm_way = cast(osmium.osm.Way, entity)  # the file is read for nodes and ways only
        stats.ways_read += 1
        road_class = kept_class(osm_way.tags)
        if road_class is None or (classes is not None and road_class not in classes):
            continue
        tags = tuple((k, value) for k in EDGE_TAGS if (value := osm_way.tags.get(k)) is not None)
        before = len(refs)
        refs.extend(n.ref for n in osm_way.nodes)
        way_ids.append(osm_way.id)
        way_sizes.append(len(refs) - before)
        way_tags.append(tagsets.setdefault((road_class, tags), len(tagsets)))
        rule, forward, backward = direction(osm_way.tags)
        way_rules.append(RULES.index(rule))
        way_forward.append(forward)
        way_backward.append(backward)

    ways = np.frombuffer(way_ids, dtype=np.int64)
    sizes = np.frombuffer(way_sizes, dtype=np.int64)
    # A way id seen twice is read once (its first occurrence); a node repeated back to back in a
    # way once.
    first = np.zeros(len(ways), dtype=np.bool_)
    first[np.unique(ways, return_index=True)[1]] = True
    stats.ways_kept = int(first.sum())
    owner = np.repeat(np.arange(len(ways)), sizes)
    flat = np.frombuffer(refs, dtype=np.int64)
    repeated = np.zeros(len(flat), dtype=np.bool_)
    repeated[1:] = (flat[1:] == flat[:-1]) & (owner[1:] == owner[:-1])
    keep = first[owner] & ~repeated
    flat, owner = flat[keep], owner[keep]
    starts = np.searchsorted(owner, np.flatnonzero(first))  # each kept way's first ref

    # Junctions: referenced at least twice by the kept ways, or the first or last node of one.
    _, inverse, counts = np.unique(flat, return_inverse=True, return_counts=True)
    junction = counts >= 2
    ends = np.append(starts[1:], len(flat))[: len(starts)] - 1  # none when no way is kept
    junction[inverse[starts]] = True
    junction[inverse[ends]] = True
    # Segments: between consecutive junctions of one way, in way order.
    cuts = np.flatnonzero(junction[inverse])
    same = owner[cuts[:-1]] == owner[cuts[1:]]
    a, b = cuts[:-1][same], cuts[1:][same]
    sizes = b - a + 1
    indptr = np.zeros(len(a) + 1, dtype=np.int64)
    np.cumsum(sizes, out=indptr[1:])
    at = np.repeat(a - indptr[:-1], sizes) + np.arange(indptr[-1])
    ref_id = flat[at]
    seg_way_index = owner[a]

    ids = np.frombuffer(node_ids, dtype=np.int64)
    missing = ~np.isin(ref_id, ids)
    if missing.any():
        raise ValueError(f"{roads.name}: ways reference node {ref_id[missing][0]}, not in it")
    order = np.argsort(ids, kind="stable")
    position = order[np.searchsorted(ids[order], ref_id)]  # file position of every ref
    lon = np.frombuffer(fixed_lon, dtype=np.int32)[position] / FIXED
    lat = np.frombuffer(fixed_lat, dtype=np.int32)[position] / FIXED
    x, y = pyproj.Transformer.from_crs(4326, crs_epsg, always_xy=True).transform(lon, lat)
    ref_x, ref_y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    lengths = _lengths(ref_x, ref_y, indptr)

    # Nodes: the segments' ends, in order of first appearance (each segment's first, then last).
    end_at = np.column_stack([indptr[:-1], indptr[1:] - 1]).ravel()
    end_id = ref_id[end_at]
    appear = np.sort(np.unique(end_id, return_index=True)[1])
    node_at = end_at[appear]
    node_id = ref_id[node_at]
    sorter = np.argsort(node_id)
    node_of_end = sorter[np.searchsorted(node_id[sorter], end_id)].astype(np.int32)

    rule = np.frombuffer(way_rules, dtype=np.int8)[seg_way_index]
    forward = np.frombuffer(way_forward, dtype=np.bool_)[seg_way_index]
    backward = np.frombuffer(way_backward, dtype=np.bool_)[seg_way_index]
    graph = RawGraph(
        crs_epsg=crs_epsg,
        node_id=node_id,
        node_x=ref_x[node_at],
        node_y=ref_y[node_at],
        node_lon=lon[node_at],
        node_lat=lat[node_at],
        seg_u=node_of_end[0::2],
        seg_v=node_of_end[1::2],
        seg_way=ways[seg_way_index],
        seg_tags=np.frombuffer(way_tags, dtype=np.int32)[seg_way_index],
        seg_forward=forward,
        seg_backward=backward,
        seg_length=lengths,
        ref_indptr=indptr,
        ref_id=ref_id,
        ref_x=ref_x,
        ref_y=ref_y,
        tagsets=list(tagsets),
    )
    stats.segments = len(a)
    stats.nodes = len(node_id)
    stats.edges = graph.edges
    per_rule = np.bincount(rule, weights=forward.astype(np.int64) + backward, minlength=len(RULES))
    stats.edges_by_rule = {r: int(per_rule[RULES.index(r)]) for r in sorted(RULES)}
    stats.edges_by_rule = {r: n for r, n in stats.edges_by_rule.items() if n}
    return graph, stats


def _lengths(
    x: NDArray[np.float64], y: NDArray[np.float64], indptr: NDArray[np.int64]
) -> NDArray[np.float64]:
    """GEOS's length of each run of coordinates, `LENGTH_CHUNK` runs at a time."""
    lengths = np.empty(len(indptr) - 1, dtype=np.float64)
    for lo in range(0, len(lengths), LENGTH_CHUNK):
        hi = min(lo + LENGTH_CHUNK, len(lengths))
        a, b = int(indptr[lo]), int(indptr[hi])
        runs = np.repeat(np.arange(hi - lo), np.diff(indptr[lo : hi + 1]))
        lengths[lo:hi] = shapely.length(shapely.linestrings(x[a:b], y[a:b], indices=runs))
    return lengths


def save_stage(graph: RawGraph, stats: BuildStats, build_dir: Path) -> None:
    """Write the graph as the `stages/build/` stage and its stats to `stages/build.json`."""
    arrays = {f.name: getattr(graph, f.name) for f in fields(graph)}
    del arrays["crs_epsg"], arrays["tagsets"]
    tables = {"crs_epsg": graph.crs_epsg, "tagsets": graph.tagsets}
    write_stage(layout.stage_file(build_dir, "build"), arrays, tables)
    summary = layout.stage_file(build_dir, "build.json")
    summary.write_text(json.dumps(asdict(stats), indent=2) + "\n")


def load_stage(build_dir: Path) -> RawGraph:
    """The `stages/build/` stage, its arrays memory-mapped."""
    arrays, tables = read_stage(layout.stage_file(build_dir, "build"))
    tagsets: list[Any] = tables["tagsets"]
    return RawGraph(
        crs_epsg=int(tables["crs_epsg"]),
        tagsets=[(c, tuple((k, v) for k, v in tags)) for c, tags in tagsets],
        **arrays,
    )
