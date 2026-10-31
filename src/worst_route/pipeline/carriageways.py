"""Cleanup, step 3: link the two carriageways of a divided road as twins (`docs/PLAN.md` §2.4e).

OSM maps each direction of a divided road as its own one-way way, so going north on I-5 and
later south on the same stretch would otherwise be legal. Using an edge bans its twins.

`find_twins` runs on the stage A graph, before stage B makes stretches of road internal to its
super-junctions; `consolidate.stage_b` carries the table across. Candidates are one-way edges of
`config.TWIN_CLASSES` (no links, no roundabout rings); their signature is `names.road_signature`
(the ref set, else the name), and an edge with neither is a candidate only in
`TWIN_GEOMETRY_ONLY_CLASSES`, where all signature-less edges pair with each other. Detection
works on the segments of each candidate's geometry (consecutive coordinate pairs), leaving out
the segment stage A added at a cluster end (it runs to the cluster's centre, not along the
road). A `shapely.STRtree` finds, for each segment of edge E, the segments within E's lateral
distance `TWIN_LATERAL_M[class]`; segment `s` of E pairs with segment `t` of F when E ≠ F, their
signatures are equal, their names without directional words are equal when both have one and
neither is a motorway (a one-way couplet a block apart shares a ref, not a name; freeway
carriageways carry alias names), their bearings differ by 180° ± `TWIN_BEARING_TOL`, each one's
midpoint lies within that distance of the other's line, and the foot of at least one of those
perpendiculars falls on the other segment (the two run side by side): two edges leaving one
crossing in opposite directions on opposite carriageways, end to end, do not pair. E and F are
twins when any of their segments pair, unless they share an OSM way: a way curling back beside
itself (a hairpin ramp, a loop) is one road, and real carriageways are always separate ways; or
unless their `osm_nodes` are the same run reversed: two ways over the same nodes in opposite
directions are one road mapped twice (a duplicate, an import artefact), not two carriageways.
Such a pair is counted in `same_road_pairs` and left out; `enrich` makes the two edges one road
unit, as it does the two directions of a two-way road, and the engine never drives both. So the
relation is symmetric and never holds between the two directions of one road. Segments of zero
length are left out.
`G.graph["twins"]` maps each edge key `(u, v, key)` with a twin to the sorted tuple of its
twins' keys.
"""

from dataclasses import dataclass

import networkx as nx
import numpy as np
import shapely

from worst_route.config import (
    TWIN_BEARING_TOL,
    TWIN_CLASSES,
    TWIN_GEOMETRY_ONLY_CLASSES,
    TWIN_LATERAL_M,
)
from worst_route.pipeline.build import ROUNDABOUT_JUNCTIONS
from worst_route.pipeline.names import RoadSignature, name_without_directions, road_signature

type EdgeKey = tuple[int, int, int]


@dataclass
class TwinStats:
    """What twin detection found."""

    candidates: int = 0  # candidate edges
    paired: int = 0  # candidate edges with at least one twin
    pairs: int = 0  # unordered twin pairs
    same_road_pairs: int = 0  # unordered pairs left out as one road mapped twice
    motorway_trunk_m: float = 0.0  # length of one-way motorway and trunk edges
    coverage: float | None = None  # the fraction of that length with a twin (None if none)


def find_twins(graph: nx.MultiDiGraph) -> TwinStats:
    """Set `graph.graph["twins"]` and return what was found."""
    stats = TwinStats()
    keys: list[EdgeKey] = []
    classes: list[str] = []
    lengths: list[float] = []
    signature_of: list[int] = []
    starts: list[np.ndarray] = []
    ends: list[np.ndarray] = []
    owners: list[np.ndarray] = []
    numbers: dict[RoadSignature | None, int] = {None: -1}
    name_of: list[int] = []
    names: dict[str | None, int] = {None: -1}
    for u, v, k, data in sorted(graph.edges(keys=True, data=True), key=lambda e: e[:3]):
        road_class = data["highway"]
        if not data["oneway"] or road_class not in TWIN_CLASSES or _is_roundabout(data):
            continue
        signature = road_signature(data)
        if signature is None and road_class not in TWIN_GEOMETRY_ONLY_CLASSES:
            continue
        coords = np.asarray(data["geometry"].coords)
        first = 0 if graph.nodes[u]["kind"] == "plain" else 1
        last = len(coords) if graph.nodes[v]["kind"] == "plain" else len(coords) - 1
        coords = coords[first:last]
        moves = np.any(coords[1:] != coords[:-1], axis=1)  # a repeated point has no bearing
        starts.append(coords[:-1][moves])
        ends.append(coords[1:][moves])
        owners.append(np.full(int(moves.sum()), len(keys)))
        keys.append((u, v, k))
        classes.append(road_class)
        lengths.append(data["length"])
        signature_of.append(numbers.setdefault(signature, len(numbers) - 1))
        # Freeway carriageways carry alias names (Banfield Freeway, Columbia River Highway) and
        # couplets are never motorways, so a motorway's name is not compared.
        name = None
        if "name" in data and road_class != "motorway":
            name = name_without_directions(data["name"]) or None
        name_of.append(names.setdefault(name, len(names) - 1))
    stats.candidates = len(keys)

    twins: dict[EdgeKey, set[EdgeKey]] = {}
    same_road: set[tuple[EdgeKey, EdgeKey]] = set()
    if keys:
        a, b, owner = np.vstack(starts), np.vstack(ends), np.concatenate(owners)
        segments = np.asarray(shapely.linestrings(np.stack([a, b], axis=1)))
        reach = np.asarray([TWIN_LATERAL_M[c] for c in classes])[owner]
        q, t = shapely.STRtree(segments).query(segments, predicate="dwithin", distance=reach)
        signatures = np.asarray(signature_of)
        named = np.asarray(name_of)
        nq, nt = named[owner[q]], named[owner[t]]
        keep = (owner[q] != owner[t]) & (signatures[owner[q]] == signatures[owner[t]])
        keep &= (nq < 0) | (nt < 0) | (nq == nt)
        q, t = q[keep], t[keep]
        delta = b - a
        bearing = np.degrees(np.arctan2(delta[:, 0], delta[:, 1]))
        turn = (bearing[q] - bearing[t] + 180.0) % 360.0 - 180.0
        keep = np.abs(turn) >= 180.0 - TWIN_BEARING_TOL
        q, t = q[keep], t[keep]
        middle = (a + b) / 2
        on_t, near_t = _perpendicular(middle[q], a[t], b[t], reach[q])
        on_q, near_q = _perpendicular(middle[t], a[q], b[q], reach[q])
        keep = near_t & near_q & (on_t | on_q)
        for i, j in {(int(owner[x]), int(owner[y])) for x, y in zip(q[keep], t[keep], strict=True)}:
            e, f = keys[i], keys[j]
            if set(graph.edges[e]["osmids"]) & set(graph.edges[f]["osmids"]):
                continue  # one way curling back beside itself is one road, not two carriageways
            if graph.edges[e]["osm_nodes"] == graph.edges[f]["osm_nodes"][::-1]:
                same_road.add((min(e, f), max(e, f)))
                continue  # one road mapped twice, as two ways in opposite directions
            twins.setdefault(e, set()).add(f)
            twins.setdefault(f, set()).add(e)
    graph.graph["twins"] = {e: tuple(sorted(fs)) for e, fs in sorted(twins.items())}

    stats.paired = len(twins)
    stats.pairs = sum(map(len, twins.values())) // 2
    stats.same_road_pairs = len(same_road)
    major = [i for i, c in enumerate(classes) if c in TWIN_GEOMETRY_ONLY_CLASSES]
    stats.motorway_trunk_m = float(sum(lengths[i] for i in major))
    if stats.motorway_trunk_m > 0:
        covered = sum(lengths[i] for i in major if keys[i] in twins)
        stats.coverage = covered / stats.motorway_trunk_m
    return stats


def _perpendicular(
    points: np.ndarray, a: np.ndarray, b: np.ndarray, reach: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """For each point and segment a-b: whether the foot of the perpendicular from the point to
    the segment's line falls on the segment, and whether it is at most `reach` from the point."""
    direction = b - a
    t = np.einsum("ij,ij->i", points - a, direction) / np.einsum("ij,ij->i", direction, direction)
    foot = a + t[:, None] * direction
    return (t >= 0) & (t <= 1), np.hypot(*(points - foot).T) <= reach


def _is_roundabout(data: dict[str, object]) -> bool:
    return data.get("junction") in ROUNDABOUT_JUNCTIONS
