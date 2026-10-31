"""`pipeline enrich`: what the engine and the modes read per edge and per node.

`docs/PLAN.md` §2.5; `docs/reference/pipeline.md`, enrich. `run_enrich(graph, pois)` adds to
the cleaned graph, in place, on every edge:

- `speed_kmh`: the `maxspeed` tag when `parse_maxspeed` reads it ("45 mph"; a bare number is mph,
  the US convention; "50 km/h"; a `;` list takes its first value), else the class default
  `config.SPEED_DEFAULT_MPH`; `speed_parsed` says which. `time_s` is `length / (speed_kmh / 3.6)`.
- `identity`: `names.identity_of` (the highest-class ref, else the normalised name, else None).
- `unit`: the key of the edge's road unit, the smaller key of the edge and its reverse, the edge
  whose `osm_nodes` run the other way (the other direction of a two-way road; edges with the
  same `osm_nodes` pair in key order); a one-way edge's own key. `save` numbers edges in key
  order, so a unit's id is the smaller of its two edge ids.
- `flags`: the `graph.data.EdgeFlag` bits: bridge and tunnel (the tag present and not `no`),
  toll (`toll=yes`), roundabout (`junction` roundabout or circular), link (a `*_link` class).
- `bearing_out`, leaving u, over the first `config.BEARING_SAMPLE_M` of the road, and
  `bearing_in`, arriving at v, over the last (the whole road when it is shorter): whole degrees
  clockwise from the projected CRS's north, 0-359. The road is the edge's geometry less the
  stubs cleanup added to reach a merged node's position (`road_geometry`): one coordinate at an
  end on a stage A cluster or a super-node, two where the super-node took in a stage A cluster
  at that end (the part holding the end's OSM node has more than one member). Every other
  coordinate is an OSM node's, so the bearings describe how the road arrives and leaves, not
  the stub; when nothing but stub would remain, the whole geometry.
- `toll_booths`, `level_crossings`: the POI nodes of those kinds on the edge. A POI whose OSM id
  lies inside edges counts on every edge that holds it (the two directions of a two-way road, of
  which a route uses one); one that is a node's member (a junction) counts on that node's
  lowest-key out-edge; any other is not on the graph.

and on every node:

- `delay_s`: the largest `config.NODE_DELAY_S` of the stop signs and signals among its members
  (a signal cluster is one junction, not one per signal); one that is a shape point adds none.
- `pass_through`: some in-neighbour u and out-neighbour w with u ≠ w, self-loops ignored (the
  rule `graph/spatial.py` applies within a request's mask).

and on the graph, `brands` (`index_brands`): each POI with a brand (a node, or a way's mean
position) snapped to the nearest pass-through node within `config.BRAND_SNAP_M` (a node a
route can only start or end at is skipped: such a POI is counted at a dead end), ties to the
lower node; grouped by `brand:wikidata` when present, else by the `brand` string casefolded
with its whitespace collapsed; named by the group's most common `brand` spelling, whitespace
collapsed (ties to the smaller string; else its most common `name`, else its wikidata id); kept
when it lies on at least `config.BRAND_MIN_LOCATIONS` distinct nodes. The list is `(name,
wikidata, [(node, POIs snapped there), ...])` per brand, nodes ascending, brands in (name,
wikidata) order, which is their id order in the build.

Edges are visited in key order and POIs in the table's order, so the result is deterministic.
`save_stage` pickles the graph to `stages/enrich.pkl` and writes the report to
`stages/enrich.json`; `parameters` is what the step's sidecar records.
"""

import json
import pickle
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pyarrow as pa
import pyproj
import shapely
from numpy.typing import NDArray
from scipy.spatial import cKDTree

from worst_route.config import (
    BEARING_SAMPLE_M,
    BRAND_MIN_LOCATIONS,
    BRAND_SNAP_M,
    NODE_DELAY_S,
    SPEED_DEFAULT_MPH,
    HighwayClass,
)
from worst_route.graph.data import EdgeFlag
from worst_route.pipeline import layout
from worst_route.pipeline.build import ROUNDABOUT_JUNCTIONS
from worst_route.pipeline.names import identity_of

KMH_PER_MPH = 1.609344
COLLECTIBLES = {"toll_booth": "toll_booths", "level_crossing": "level_crossings"}

type EdgeKey = tuple[int, int, int]
type BrandIndex = list[tuple[str, str | None, list[tuple[int, int]]]]

_FLAG_NAMES = {name.lower(): bit for name, bit in EdgeFlag.__members__.items()}
_MAXSPEED = re.compile(r"(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?", re.IGNORECASE)


@dataclass
class EnrichReport:
    """What enrich set, counted."""

    nodes: int = 0
    edges: int = 0
    speed_parsed: dict[str, int] = field(default_factory=dict)  # edges by class
    speed_default: dict[str, int] = field(default_factory=dict)  # edges by class
    identities: int = 0  # distinct identities
    edges_with_identity: int = 0
    units: int = 0
    two_way_pairs: int = 0
    flags: dict[str, int] = field(default_factory=dict)  # edges with each flag
    # per kind: found (POI nodes), on_edges, at_junctions, off_graph, edges (with a count)
    collectibles: dict[str, dict[str, int]] = field(default_factory=dict)
    # per kind: found, at_junctions (a delay), shape_points (no delay), off_graph
    delays: dict[str, dict[str, int]] = field(default_factory=dict)
    delay_nodes: int = 0  # nodes with a delay
    pass_through: int = 0  # nodes
    # brand POIs: found, snapped, at_dead_ends, too_far; brands (groups), kept, and the kept
    # brands' rows (a brand at a node) and distinct nodes
    brands: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


def parameters() -> dict[str, Any]:
    """The config values enrich depends on, JSON-ready."""
    return {
        "SPEED_DEFAULT_MPH": {c.name.lower(): mph for c, mph in SPEED_DEFAULT_MPH.items()},
        "NODE_DELAY_S": dict(NODE_DELAY_S),
        "BEARING_SAMPLE_M": BEARING_SAMPLE_M,
        "BRAND_SNAP_M": BRAND_SNAP_M,
        "BRAND_MIN_LOCATIONS": BRAND_MIN_LOCATIONS,
    }


def parse_maxspeed(value: str) -> float | None:
    """The speed a `maxspeed` tag gives, in km/h, or None when it gives none."""
    match = _MAXSPEED.fullmatch(value.split(";")[0].strip())
    if match is None or float(match.group(1)) <= 0:
        return None
    speed = float(match.group(1))
    return speed * KMH_PER_MPH if (match.group(2) or "mph").lower() == "mph" else speed


def bearings(geometries: list[shapely.LineString]) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """(in, out) per line: degrees clockwise from north over its last and first metres."""
    lines = np.asarray(geometries, dtype=object)
    lengths = shapely.length(lines)
    step = np.minimum(BEARING_SAMPLE_M, lengths)
    start = shapely.get_coordinates(shapely.get_point(lines, 0))
    end = shapely.get_coordinates(shapely.get_point(lines, -1))
    ahead = shapely.get_coordinates(shapely.line_interpolate_point(lines, step))
    behind = shapely.get_coordinates(shapely.line_interpolate_point(lines, lengths - step))
    return _bearing(behind, end), _bearing(start, ahead)


def road_geometry(graph: nx.MultiDiGraph, key: EdgeKey) -> shapely.LineString:
    """The edge's geometry less the stubs cleanup added at its merged ends (the whole geometry
    when nothing but stub would remain)."""
    u, v, _ = key
    data = graph.edges[key]
    coords = list(data["geometry"].coords)
    start = _stubs(graph, u, data["osm_nodes"][0])
    road = coords[start : len(coords) - _stubs(graph, v, data["osm_nodes"][-1])]
    line = shapely.LineString(road) if len(road) >= 2 else None
    return line if line is not None and line.length > 0 else data["geometry"]


def _stubs(graph: nx.MultiDiGraph, node: int, end: int) -> int:
    """How many stub coordinates cleanup added at an edge's end on `node`, whose OSM node there
    is `end`: one for a stage A cluster, one for a super-node and one more when the part of it
    holding `end` was a stage A cluster (a part of more than one member)."""
    kind = graph.nodes[node]["kind"]
    if kind == "plain":
        return 0
    if kind == "cluster":
        return 1
    part = next(p for p in graph.graph["superjunctions"][node]["parts"] if end in p)
    return 1 + (len(part) > 1)


def _bearing(a: NDArray[np.float64], b: NDArray[np.float64]) -> NDArray[np.int64]:
    d = b - a
    degrees = np.degrees(np.arctan2(d[:, 0], d[:, 1]))
    return np.rint(degrees).astype(np.int64) % 360


def run_enrich(graph: nx.MultiDiGraph, pois: pa.Table) -> EnrichReport:
    """Enrich the cleaned graph in place (`pois` as `filter` wrote them); report what was set."""
    clock = time.perf_counter()
    report = EnrichReport(nodes=graph.number_of_nodes(), edges=graph.number_of_edges())
    keys: list[EdgeKey] = sorted(graph.edges(keys=True))
    parsed: Counter[str] = Counter()
    default: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    identities = set()
    for key in keys:
        data = graph.edges[key]
        speed = parse_maxspeed(data["maxspeed"]) if "maxspeed" in data else None
        data["speed_parsed"] = speed is not None
        (parsed if speed is not None else default)[data["highway"]] += 1
        if speed is None:
            speed = SPEED_DEFAULT_MPH[HighwayClass[data["highway"].upper()]] * KMH_PER_MPH
        data["speed_kmh"] = speed
        data["time_s"] = data["length"] / (speed / 3.6)
        data["identity"] = identity_of(data)
        if data["identity"] is not None:
            identities.add(data["identity"])
            report.edges_with_identity += 1
        data["flags"] = edge_flags(data)
        flags.update(name for name, bit in _FLAG_NAMES.items() if data["flags"] & bit)
        data["toll_booths"] = data["level_crossings"] = 0
    report.speed_parsed = dict(sorted(parsed.items()))
    report.speed_default = dict(sorted(default.items()))
    report.identities = len(identities)
    report.flags = {name: flags[name] for name in _FLAG_NAMES}

    report.units, report.two_way_pairs = _units(graph, keys)
    if keys:
        inward, outward = bearings([road_geometry(graph, key) for key in keys])
        for key, b_in, b_out in zip(keys, inward.tolist(), outward.tolist(), strict=True):
            graph.edges[key]["bearing_in"], graph.edges[key]["bearing_out"] = b_in, b_out
    _place_pois(graph, keys, pois, report)

    for node, data in graph.nodes(data=True):
        before = {u for u in graph.predecessors(node) if u != node}
        after = {w for w in graph.successors(node) if w != node}
        data["pass_through"] = bool(before and after and len(before | after) >= 2)
        report.pass_through += data["pass_through"]
    graph.graph["brands"], report.brands = index_brands(graph, pois)
    report.seconds = round(time.perf_counter() - clock, 3)
    return report


def edge_flags(data: dict[str, Any]) -> int:
    """The `EdgeFlag` bits of an edge, from its class and tags."""
    flags = EdgeFlag(0)
    if data.get("bridge", "no") != "no":
        flags |= EdgeFlag.BRIDGE
    if data.get("tunnel", "no") != "no":
        flags |= EdgeFlag.TUNNEL
    if data.get("toll") == "yes":
        flags |= EdgeFlag.TOLL
    if data.get("junction") in ROUNDABOUT_JUNCTIONS:
        flags |= EdgeFlag.ROUNDABOUT
    if data["highway"].endswith("_link"):
        flags |= EdgeFlag.LINK
    return int(flags)


def _units(graph: nx.MultiDiGraph, keys: list[EdgeKey]) -> tuple[int, int]:
    """Set each edge's `unit`; return the number of units and of two-way pairs."""
    by_nodes: dict[tuple[int, ...], list[EdgeKey]] = defaultdict(list)
    for key in keys:  # ascending, so each list is too
        graph.edges[key]["unit"] = key
        by_nodes[tuple(graph.edges[key]["osm_nodes"])].append(key)
    pairs = 0
    for nodes, forward in by_nodes.items():
        if nodes < nodes[::-1]:  # each pair of directions once; osm_nodes never repeat an id
            for a, b in zip(forward, by_nodes.get(nodes[::-1], []), strict=False):
                graph.edges[a]["unit"] = graph.edges[b]["unit"] = min(a, b)
                pairs += 1
    return len(keys) - pairs, pairs


def _place_pois(
    graph: nx.MultiDiGraph, keys: list[EdgeKey], pois: pa.Table, report: EnrichReport
) -> None:
    """Count the collectibles on edges and set each node's delay."""
    member_of = {m: node for node, members in graph.nodes(data="members") for m in members}
    inside: dict[int, list[EdgeKey]] = defaultdict(list)
    for key in keys:
        for osm_id in graph.edges[key]["osm_nodes"][1:-1]:
            inside[osm_id].append(key)
    for _, data in graph.nodes(data=True):
        data["delay_s"] = 0.0
    counts = {kind: Counter[str]() for kind in (*COLLECTIBLES, *NODE_DELAY_S)}
    for row in pois.select(["osm_type", "osm_id", "kind"]).to_pylist():
        kind, osm_id = row["kind"], row["osm_id"]
        if row["osm_type"] != "n" or kind not in counts:
            continue
        counts[kind]["found"] += 1
        node = member_of.get(osm_id)
        if kind in NODE_DELAY_S:
            if node is not None:
                data = graph.nodes[node]
                data["delay_s"] = max(data["delay_s"], NODE_DELAY_S[kind])
                counts[kind]["at_junctions"] += 1
            else:
                counts[kind]["shape_points" if osm_id in inside else "off_graph"] += 1
            continue
        if node is not None:
            edges = [min(graph.out_edges(node, keys=True))]
            counts[kind]["at_junctions"] += 1
        elif osm_id in inside:
            edges = inside[osm_id]
            counts[kind]["on_edges"] += 1
        else:
            counts[kind]["off_graph"] += 1
            continue
        for key in edges:
            graph.edges[key][COLLECTIBLES[kind]] += 1
    for kind, attribute in COLLECTIBLES.items():
        found = counts[kind]
        report.collectibles[kind] = {
            name: found[name] for name in ("found", "on_edges", "at_junctions", "off_graph")
        }
        report.collectibles[kind]["edges"] = sum(1 for k in keys if graph.edges[k][attribute])
    for kind in NODE_DELAY_S:
        found = counts[kind]
        report.delays[kind] = {
            name: found[name] for name in ("found", "at_junctions", "shape_points", "off_graph")
        }
    report.delay_nodes = sum(1 for _, delay in graph.nodes(data="delay_s") if delay > 0)


def index_brands(graph: nx.MultiDiGraph, pois: pa.Table) -> tuple[BrandIndex, dict[str, int]]:
    """The brand index of the graph (the module docstring) and its counts; `pass_through` set."""
    rows = [r for r in pois.to_pylist() if r["kind"] == "brand"]
    counts = {"found": len(rows), "snapped": 0, "at_dead_ends": 0, "too_far": 0}
    nodes = sorted(graph.nodes)
    xy = np.array([(graph.nodes[v]["x"], graph.nodes[v]["y"]) for v in nodes]).reshape(-1, 2)
    through = np.array([graph.nodes[v]["pass_through"] for v in nodes], dtype=np.bool_)
    to_xy = pyproj.Transformer.from_crs(4326, graph.graph["crs"], always_xy=True)
    px, py = to_xy.transform([r["lon"] for r in rows], [r["lat"] for r in rows])
    points = np.column_stack([px, py]).reshape(-1, 2)
    near = cKDTree(xy).query_ball_point(points, BRAND_SNAP_M) if len(nodes) else []

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    snapped: list[int | None] = []
    for row, point, around in zip(rows, points, near, strict=True):
        candidates = np.array(sorted(around), dtype=np.int64)
        candidates = candidates[through[candidates]] if len(candidates) else candidates
        if len(candidates):
            d = np.hypot(*(xy[candidates] - point).T)
            snapped.append(nodes[int(candidates[np.lexsort((candidates, d))[0]])])
            counts["snapped"] += 1
        else:
            snapped.append(None)
            counts["at_dead_ends" if around else "too_far"] += 1
        wikidata, brand = row["brand_wikidata"], " ".join((row["brand"] or "").split())
        if wikidata or brand:  # an empty brand tag alone names nothing
            key = ("wikidata", wikidata) if wikidata else ("brand", brand.casefold())
            groups[key].append({**row, "node": snapped[-1]})

    index: BrandIndex = []
    for key, members in groups.items():
        at = Counter(m["node"] for m in members if m["node"] is not None)
        if len(at) < BRAND_MIN_LOCATIONS:
            continue
        spellings = Counter(" ".join((m["brand"] or "").split()) for m in members)
        names = Counter(m["name"] for m in members if m["name"])
        del spellings[""]  # no brand tag, or an empty one
        common = spellings or names
        name = min(common.items(), key=lambda kv: (-kv[1], kv[0]))[0] if common else key[1]
        wikidata = key[1] if key[0] == "wikidata" else None
        index.append((name, wikidata, sorted(at.items())))
    index.sort(key=lambda brand: (brand[0], brand[1] or ""))
    placed = [node for _, _, at in index for node, _ in at]
    counts.update(brands=len(groups), kept=len(index), rows=len(placed), nodes=len(set(placed)))
    return index, counts


def save_stage(graph: nx.MultiDiGraph, report: EnrichReport, build_dir: Path) -> None:
    """Write the graph to `stages/enrich.pkl` and the report to `stages/enrich.json`."""
    pkl = layout.stage_file(build_dir, "enrich.pkl")
    with pkl.open("wb") as f:
        pickle.dump(graph, f, protocol=pickle.HIGHEST_PROTOCOL)
    summary = layout.stage_file(build_dir, "enrich.json")
    summary.write_text(json.dumps(asdict(report), indent=2) + "\n")
