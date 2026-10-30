"""`pipeline build`: the filtered roads as a raw directed graph in OSMnx's conventions.

`docs/PLAN.md` §2.3. `build_raw_graph` reads the roads file once with pyosmium: node locations
into arrays, and the kept ways (`exclusions.kept_class` applied again, so an unfiltered file
builds too; duplicates by id read once; a node repeated back to back in a way read once, as
OSMnx does) with the tags in `EDGE_TAGS`. A node is a **junction**
when kept ways reference it at least twice (two ways, or one way passing through it twice) or
it ends a way; every other node is a shape point. Each way is split at its junctions into
segments, and each segment becomes one edge per direction its one-way rule allows (`direction`).

The graph's nodes are the junctions, keyed by OSM node id, with `x, y` in the region's projected
CRS and `lon, lat`. An edge carries `osmid` (the way id), `highway` (the kept class, "alley" for
alleys), the way's `EDGE_TAGS` that are present (absent ones are absent, never None), `oneway`
(it may be driven one way only), `reversed` (True on the backward edge of a two-way segment, as
in OSMnx), `length` in metres from the projected geometry, and `geometry` (a projected
`LineString`) and `osm_nodes` (the segment's OSM node ids, endpoints included), both in
traversal direction. `save_stage` pickles the graph as the intermediate stage the next step
reads; the final on-disk format (`docs/PLAN.md` §2.7) never pickles.
"""

import json
import pickle
from collections import Counter
from dataclasses import asdict, dataclass, field
from itertools import groupby, pairwise
from pathlib import Path
from typing import cast

import networkx as nx
import numpy as np
import osmium
import pyproj
import shapely

from worst_route.pipeline import PIPELINE_VERSION, layout
from worst_route.pipeline.exclusions import RoadClass, Tags, kept_class

EDGE_TAGS = (
    "name",
    "ref",
    "maxspeed",
    "lanes",
    "bridge",
    "tunnel",
    "toll",
    "junction",
    "service",
    "access",
    "layer",
)

ONEWAY_FORWARD = frozenset({"yes", "true", "1"})
ONEWAY_BACKWARD = frozenset({"-1", "reverse"})
ONEWAY_BOTH = frozenset({"reversible", "alternating"})
ROUNDABOUT_JUNCTIONS = frozenset({"roundabout", "circular"})
IMPLIED_ONEWAY_HIGHWAY = frozenset({"motorway", "motorway_link"})


@dataclass
class BuildStats:
    """What the build read and made."""

    ways_read: int = 0  # way objects in the file, duplicates and unkept ones included
    ways_kept: int = 0  # distinct kept ways
    segments: int = 0
    nodes: int = 0
    edges: int = 0
    edges_by_rule: dict[str, int] = field(default_factory=dict)  # directed edges per `direction`


@dataclass
class _Way:
    refs: tuple[int, ...]
    road_class: RoadClass
    rule: str
    forward: bool
    backward: bool
    tags: dict[str, str]


def direction(tags: Tags) -> tuple[str, bool, bool]:
    """The way's one-way rule, and whether it may be driven forward (in node order) and backward.

    The first rule that matches wins: `oneway` yes/true/1, then -1/reverse, then a roundabout
    or circular junction, then a motorway or motorway link with no `oneway` tag (all one way);
    then `oneway=no`, then reversible/alternating, then anything else (all both ways).
    """
    oneway = tags.get("oneway")
    if oneway in ONEWAY_FORWARD:
        return "oneway", True, False
    if oneway in ONEWAY_BACKWARD:
        return "oneway_reverse", False, True
    if tags.get("junction") in ROUNDABOUT_JUNCTIONS:
        return "roundabout", True, False
    if oneway is None and tags.get("highway") in IMPLIED_ONEWAY_HIGHWAY:
        return "motorway_implied", True, False
    if oneway == "no":
        return "oneway_no", True, True
    if oneway in ONEWAY_BOTH:
        return "reversible", True, True
    return "two_way", True, True


def build_raw_graph(roads: Path, crs_epsg: int) -> tuple[nx.MultiDiGraph, BuildStats]:
    """The raw directed graph of the roads file, its coordinates projected to `crs_epsg`."""
    stats = BuildStats()
    node_ids: list[int] = []
    lons: list[float] = []
    lats: list[float] = []
    ways: dict[int, _Way] = {}
    for entity in osmium.FileProcessor(roads, osmium.osm.NODE | osmium.osm.WAY):
        if isinstance(entity, osmium.osm.Node):
            location = entity.location
            node_ids.append(entity.id)
            lons.append(location.lon)
            lats.append(location.lat)
            continue
        osm_way = cast(osmium.osm.Way, entity)  # the file is read for nodes and ways only
        stats.ways_read += 1
        road_class = kept_class(osm_way.tags)
        if road_class is None or osm_way.id in ways:
            continue
        rule, forward, backward = direction(osm_way.tags)
        tags = {key: value for key in EDGE_TAGS if (value := osm_way.tags.get(key)) is not None}
        refs = tuple(ref for ref, _ in groupby(n.ref for n in osm_way.nodes))
        ways[osm_way.id] = _Way(refs, road_class, rule, forward, backward, tags)
    stats.ways_kept = len(ways)

    references = Counter(ref for way in ways.values() for ref in way.refs)
    junctions = {ref for ref, count in references.items() if count >= 2}
    junctions.update(end for way in ways.values() for end in (way.refs[0], way.refs[-1]))

    # Segments as runs of a flat array of node refs: segment k is flat[starts[k]:starts[k + 1]].
    segment_way: list[int] = []
    starts: list[int] = []
    flat: list[int] = []
    for way_id, way in ways.items():
        cuts = [i for i, ref in enumerate(way.refs) if ref in junctions]
        for a, b in pairwise(cuts):
            segment_way.append(way_id)
            starts.append(len(flat))
            flat.extend(way.refs[a : b + 1])
    starts.append(len(flat))

    ids = np.asarray(node_ids, dtype=np.int64)
    refs_array = np.asarray(flat, dtype=np.int64)
    missing = ~np.isin(refs_array, ids)
    if missing.any():
        raise ValueError(f"{roads.name}: ways reference node {refs_array[missing][0]}, not in it")
    order = np.argsort(ids, kind="stable")
    at = order[np.searchsorted(ids[order], refs_array)]  # file position of every ref in flat
    to_crs = pyproj.Transformer.from_crs(4326, crs_epsg, always_xy=True)
    lon_at, lat_at = np.asarray(lons)[at], np.asarray(lats)[at]
    x_at, y_at = to_crs.transform(lon_at, lat_at)
    bounds = np.asarray(starts)
    segment_of = np.repeat(np.arange(len(segment_way)), np.diff(bounds))
    geometries = np.asarray(shapely.linestrings(x_at, y_at, indices=segment_of))
    reversed_geometries = np.asarray(shapely.reverse(geometries))
    lengths = np.asarray(shapely.length(geometries))

    graph = nx.MultiDiGraph(crs=f"EPSG:{crs_epsg}", pipeline_version=PIPELINE_VERSION)
    rules: Counter[str] = Counter()
    for k, way_id in enumerate(segment_way):
        way = ways[way_id]
        first, last = int(bounds[k]), int(bounds[k + 1]) - 1
        for i in (first, last):
            if flat[i] not in graph:
                graph.add_node(
                    flat[i],
                    x=float(x_at[i]),
                    y=float(y_at[i]),
                    lon=float(lon_at[i]),
                    lat=float(lat_at[i]),
                )
        osm_nodes = tuple(flat[first : last + 1])
        common = {
            "osmid": way_id,
            "highway": way.road_class,
            **way.tags,
            "oneway": way.forward != way.backward,
            "length": float(lengths[k]),
        }
        if way.forward:
            graph.add_edge(
                osm_nodes[0],
                osm_nodes[-1],
                **common,
                reversed=False,
                geometry=geometries[k],
                osm_nodes=osm_nodes,
            )
        if way.backward:
            graph.add_edge(
                osm_nodes[-1],
                osm_nodes[0],
                **common,
                reversed=way.forward,
                geometry=reversed_geometries[k],
                osm_nodes=osm_nodes[::-1],
            )
        rules[way.rule] += way.forward + way.backward

    stats.segments = len(segment_way)
    stats.nodes = graph.number_of_nodes()
    stats.edges = graph.number_of_edges()
    stats.edges_by_rule = dict(sorted(rules.items()))
    return graph, stats


def save_stage(graph: nx.MultiDiGraph, stats: BuildStats, build_dir: Path) -> None:
    """Write the graph to `stages/build.pkl` and its stats to `stages/build.json`."""
    pkl = layout.stage_file(build_dir, "build.pkl")
    pkl.parent.mkdir(parents=True, exist_ok=True)
    with pkl.open("wb") as f:
        pickle.dump(graph, f, protocol=pickle.HIGHEST_PROTOCOL)
    summary = layout.stage_file(build_dir, "build.json")
    summary.write_text(json.dumps(asdict(stats), indent=2) + "\n")
