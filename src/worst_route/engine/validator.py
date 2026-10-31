"""The validator: proves a finished route legal from the raw data (`docs/PLAN.md` §8.1).

`validate_route(data, route, stop_nodes=, node_mask=, banned_edges=, banned_turn_classes=0,
hard=False, alphabet=False)` runs every check below
on a route's `nodes`, `edges`, `stop_idx` and reported `metrics`, and returns a
`ValidationReport`: one `Check` per rule, each with `ok`, the first offending index (a position
in `nodes` or `edges`, an edge for `osm_ids`, a stop for `stops`) and a detail. `report.ok` is
their conjunction. `stop_nodes` are the snapped stops, `node_mask` and `banned_edges` the
request's masks (the active nodes; the edges the tier excludes outside the stops' bubbles),
recomputed from the request by the caller, not taken from the engine's state.

1. `chain`: `edges[k]` runs `nodes[k]` → `nodes[k + 1]` (so one-ways are respected), ids in
   range, and both ends of every edge in the active mask.
2. `vertex_simple`: no node twice, except a round trip's start at both ends.
3. `edge_simple`: no edge twice, no edge with its twin, no road unit twice.
4. `osm_ids`: the raw-data proof. The route's nodes are expanded to the original OSM node ids
   merged into each (`member_osm_ids`: a cluster's or a super-junction's members, a plain
   node's own id), a round trip's closing node left out; no id appears twice, so no OSM
   junction is passed twice, even one two build nodes hold, whatever the build's node ids and
   the engine's bookkeeping say. The OSM nodes inside edges need no check: simplification
   removes only a node with one through path per direction (`pipeline/simplify.py`), so such a
   node lies once inside one edge, or inside the two directions of one road unit, and is no
   node's member (`pipeline save` refuses a build where that fails); passing it twice means
   driving an edge or a unit twice, which `edge_simple` refuses. Its index is the edge into the
   node passed twice.
5. `transitions`: every (edges[k-1], edges[k]) turn allowed at a node with transition rows.
6. `stops`: `nodes[stop_idx[s]] == stop_nodes[s]`, strictly increasing positions from the
   first node to the last.
7. `constraints`: every node in the active mask, no edge in `banned_edges`, under a strict
   turn constraint (`banned_turn_classes`, its class bits) no turn of a class it refuses: each
   turn classed again here from the edges' bearings (`TURN_STRAIGHT_DEG`, `TURN_UTURN_DEG`),
   not with the engine's table; and under hard mode (`hard`) each road in one stretch: the
   positions of the edges of each identity (`edge_identity`, −1 free) one run, so a road the
   route leaves never comes back, read here from the build's identities, not the engine's.
8. `metrics`: the reported length and time are finite and equal the ones recomputed from the
   edges within a relative 1e-6, reported `items` (the metrics' counters carry them for a
   collecting route and for a route read from a file) equal the route's recount, and a
   reported `brand` (a pilgrimage's `{id, name, visits, of}`) equals its recount from the
   build's brand index, `of` within the active mask, and a reported `alphabet` (an alphabet
   route's `{letters, sequence, roads, alpha_from, free_from}`) equals its recount from the
   route's edges and the build's names, given its `free_from`.
9. `alphabet`, only for a request whose objective is the alphabet route (`alphabet=True`): the
   route reports its letters (`free_from`, where its free last leg starts, within the route),
   and its alphabet part, from its first edge of letter A to `free_from`, reads A, B, C, ...
   (Z then A again): the letters of its lettered edges, each edge's from its name
   (`pipeline.names.road_letter`; unnamed edges, links and numbered streets have none), equal
   ones in a row counting once. The first leg, before the first A road, and the last leg, from
   `free_from`, are free: they may carry any roads.

It deliberately uses nothing of `engine/route.py`'s bookkeeping, so a bug there cannot hide
here too; it recounts the items with `objectives.collect.count_items` and a brand's visits
with `objectives.brand.brand_counts`, which read the route's arrays and the build's alone, and
reads an alphabet route's letters itself (sharing only what a name's letter is,
`road_letter`). A
route whose ids are out of range fails `chain`, and the checks that would index with them fail
as skipped. `ValidationFailed` carries a failing report.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from worst_route.config import TURN_STRAIGHT_DEG, TURN_UTURN_DEG
from worst_route.engine.objectives.brand import brand_counts
from worst_route.engine.objectives.collect import count_items
from worst_route.graph.backend import TURN_LEFT, TURN_RIGHT, TURN_U
from worst_route.graph.data import EdgeFlag, GraphData
from worst_route.pipeline.names import road_letter

METRICS_TOLERANCE = 1e-6  # relative, docs/PLAN.md §8.1


class Measured(Protocol):
    """The reported numbers check 8 recomputes."""

    @property
    def length_m(self) -> float: ...

    @property
    def time_s(self) -> float: ...

    @property
    def counters(self) -> Mapping[str, Any]: ...


class RouteLike(Protocol):
    """What the validator reads from a route: a `Route` or a route read from a file."""

    @property
    def nodes(self) -> NDArray[np.int32]: ...

    @property
    def edges(self) -> NDArray[np.int32]: ...

    @property
    def stop_idx(self) -> tuple[int, ...]: ...

    @property
    def metrics(self) -> Measured: ...


@dataclass(frozen=True)
class Check:
    """One rule's result: `index` is the first offending position, None when ok."""

    name: str
    ok: bool
    index: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class ValidationReport:
    """Every check of one route."""

    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        """Whether every check passed."""
        return all(c.ok for c in self.checks)

    def check(self, name: str) -> Check:
        """The check called `name`."""
        return next(c for c in self.checks if c.name == name)

    def lines(self) -> list[str]:
        """One line per check, for the CLI."""
        return [
            f"{c.name}: ok"
            if c.ok
            else f"{c.name}: FAILED{'' if c.index is None else f' at {c.index}'}: {c.detail}"
            for c in self.checks
        ]

    def as_dict(self) -> dict[str, Any]:
        """The report as JSON-native data."""
        return {
            "ok": self.ok,
            "checks": [
                {"name": c.name, "ok": c.ok, "index": c.index, "detail": c.detail}
                for c in self.checks
            ],
        }


class ValidationFailed(Exception):  # noqa: N818 (the name the design uses)
    """A route the validator rejected; never a result."""

    def __init__(self, report: ValidationReport) -> None:
        failed = "; ".join(line for line in report.lines() if "FAILED" in line)
        super().__init__(f"route failed validation: {failed}")
        self.report = report


NAMES = (
    "chain",
    "vertex_simple",
    "edge_simple",
    "osm_ids",
    "transitions",
    "stops",
    "constraints",
    "metrics",
)


def validate_route(
    data: GraphData,
    route: RouteLike,
    *,
    stop_nodes: Sequence[int],
    node_mask: NDArray[np.bool_],
    banned_edges: NDArray[np.bool_],
    banned_turn_classes: int = 0,
    hard: bool = False,
    alphabet: bool = False,
) -> ValidationReport:
    """Run every check on `route` (`alphabet` for an alphabet route's request); see the module
    docstring."""
    nodes = np.asarray(route.nodes, dtype=np.int64)
    edges = np.asarray(route.edges, dtype=np.int64)
    names = (*NAMES, "alphabet") if alphabet else NAMES
    invalid = _invalid_ids(data, nodes, edges)
    if invalid is not None:
        skipped = [
            Check(name, False, None, "skipped: the route's ids are invalid") for name in names
        ]
        return ValidationReport((invalid, *skipped[1:]))
    round_trip = len(stop_nodes) > 0 and stop_nodes[0] == stop_nodes[-1]
    checks: tuple[Check, ...] = (
        _chain(data, nodes, edges, node_mask),
        _vertex_simple(nodes, round_trip),
        _edge_simple(data, edges),
        _osm_ids(data, nodes, round_trip),
        _transitions(data, nodes, edges),
        _stops(nodes, route.stop_idx, stop_nodes),
        _constraints(data, nodes, edges, node_mask, banned_edges, banned_turn_classes, hard),
        _metrics(data, route, edges, node_mask),
    )
    if alphabet:
        checks = (*checks, _alphabet(data, route, edges))
    return ValidationReport(checks)


def _fail(name: str, index: int, detail: str) -> Check:
    return Check(name, False, int(index), detail)


def _invalid_ids(
    data: GraphData, nodes: NDArray[np.int64], edges: NDArray[np.int64]
) -> Check | None:
    """The failed `chain` check of a route the other checks cannot index with, or None."""
    if nodes.ndim != 1 or edges.ndim != 1 or len(edges) == 0 or len(edges) != len(nodes) - 1:
        return _fail("chain", 0, f"{len(nodes)} nodes and {len(edges)} edges")
    for what, ids, size in (("node", nodes, data.n), ("edge", edges, data.m)):
        bad = np.flatnonzero((ids < 0) | (ids >= size))
        if len(bad):
            return _fail("chain", bad[0], f"{what} {ids[bad[0]]} is not in the graph")
    return None


def _chain(
    data: GraphData, nodes: NDArray[np.int64], edges: NDArray[np.int64], mask: NDArray[np.bool_]
) -> Check:
    tail, head = data.edge_u[edges], data.edge_v[edges]
    bad = np.flatnonzero((tail != nodes[:-1]) | (head != nodes[1:]))
    if len(bad):
        k = bad[0]
        runs = f"{tail[k]} → {head[k]}, not {nodes[k]} → {nodes[k + 1]}"
        return _fail("chain", k, f"edge {edges[k]} runs {runs}")
    bad = np.flatnonzero(~(mask[tail] & mask[head]))
    if len(bad):
        return _fail("chain", bad[0], f"edge {edges[bad[0]]} leaves the active mask")
    return Check("chain", True)


def _first_repeat(values: NDArray[Any]) -> int | None:
    """The first position whose value came earlier, or None."""
    first = np.zeros(len(values), dtype=np.bool_)
    first[np.unique(values, return_index=True)[1]] = True
    repeats = np.flatnonzero(~first)
    return int(repeats[0]) if len(repeats) else None


def _vertex_simple(nodes: NDArray[np.int64], round_trip: bool) -> Check:
    k = _first_repeat(nodes[:-1] if round_trip and nodes[0] == nodes[-1] else nodes)
    if k is not None:
        return _fail("vertex_simple", k, f"node {nodes[k]} visited twice")
    return Check("vertex_simple", True)


def _edge_simple(data: GraphData, edges: NDArray[np.int64]) -> Check:
    k = _first_repeat(edges)
    if k is not None:
        return _fail("edge_simple", k, f"edge {edges[k]} driven twice")
    k = _first_repeat(data.edge_unit[edges])
    if k is not None:
        return _fail("edge_simple", k, f"road unit {data.edge_unit[edges[k]]} driven twice")
    position = np.full(data.m, -1, dtype=np.int64)
    position[edges] = np.arange(len(edges))
    starts = data.twin_indptr[edges]
    counts = data.twin_indptr[edges + 1] - starts
    rows = np.repeat(np.arange(len(edges)), counts)
    within = np.arange(len(rows)) - np.repeat(np.cumsum(counts) - counts, counts)
    twins = data.twin_edges[np.repeat(starts, counts) + within]
    at = position[twins]
    driven = at >= 0
    if driven.any():
        k = int(np.maximum(rows[driven], at[driven]).min())
        return _fail("edge_simple", k, f"edge {edges[k]} and its twin both driven")
    return Check("edge_simple", True)


def _osm_ids(data: GraphData, nodes: NDArray[np.int64], round_trip: bool) -> Check:
    walk = nodes[:-1] if round_trip and nodes[0] == nodes[-1] else nodes
    starts = data.member_indptr[walk]
    counts = data.member_indptr[walk + 1] - starts
    within = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    ids = data.member_osm_ids[np.repeat(starts, counts) + within]
    k = _first_repeat(ids)
    if k is not None:
        at = int(np.repeat(np.arange(len(walk)), counts)[k])
        return _fail("osm_ids", max(at - 1, 0), f"original OSM node {ids[k]} passed twice")
    return Check("osm_ids", True)


def _transitions(data: GraphData, nodes: NDArray[np.int64], edges: NDArray[np.int64]) -> Check:
    if len(data.trans_node) == 0 or len(edges) < 2:
        return Check("transitions", True)
    m = np.int64(data.m)
    allowed = np.sort(data.trans_in.astype(np.int64) * m + data.trans_out)
    junctions = nodes[1:-1]
    turns = edges[:-1] * m + edges[1:]
    at = np.minimum(np.searchsorted(allowed, turns), len(allowed) - 1)
    listed = allowed[at] == turns
    bad = np.flatnonzero(np.isin(junctions, data.trans_node) & ~listed)
    if len(bad):
        k = int(bad[0]) + 1
        return _fail(
            "transitions", k, f"turn {edges[k - 1]} → {edges[k]} not allowed at node {nodes[k]}"
        )
    return Check("transitions", True)


def _stops(nodes: NDArray[np.int64], stop_idx: Sequence[int], stop_nodes: Sequence[int]) -> Check:
    idx = np.asarray(stop_idx, dtype=np.int64)
    if len(idx) != len(stop_nodes) or len(idx) < 2:
        return _fail("stops", 0, f"{len(idx)} stop positions for {len(stop_nodes)} stops")
    if idx[0] != 0 or idx[-1] != len(nodes) - 1:
        return _fail(
            "stops", 0 if idx[0] != 0 else len(idx) - 1, "the stops must open and close the route"
        )
    bad = np.flatnonzero(np.diff(idx) <= 0)
    if len(bad):
        return _fail("stops", bad[0] + 1, f"stop positions {idx.tolist()} not increasing")
    bad = np.flatnonzero(nodes[idx] != np.asarray(stop_nodes))
    if len(bad):
        s = bad[0]
        return _fail("stops", s, f"stop {s} is node {stop_nodes[s]}, the route has {nodes[idx[s]]}")
    return Check("stops", True)


def _constraints(
    data: GraphData,
    nodes: NDArray[np.int64],
    edges: NDArray[np.int64],
    node_mask: NDArray[np.bool_],
    banned_edges: NDArray[np.bool_],
    banned_turn_classes: int,
    hard: bool,
) -> Check:
    bad = np.flatnonzero(~node_mask[nodes])
    if len(bad):
        return _fail("constraints", bad[0], f"node {nodes[bad[0]]} is outside the request's area")
    bad = np.flatnonzero(banned_edges[edges])
    if len(bad):
        return _fail(
            "constraints", bad[0], f"edge {edges[bad[0]]} is excluded by the request's tier"
        )
    turned = _turns(data, edges[:-1], edges[1:])
    bad = np.flatnonzero(turned & banned_turn_classes)
    if len(bad):
        turn = {TURN_LEFT: "left turn", TURN_RIGHT: "right turn", TURN_U: "U-turn"}[turned[bad[0]]]
        node = nodes[bad[0] + 1]
        detail = f"a {turn} at node {node}, which the request's strict constraint refuses"
        return _fail("constraints", bad[0] + 1, detail)
    ids = data.edge_identity[edges]
    starts = np.flatnonzero((ids >= 0) & np.concatenate(([True], ids[1:] != ids[:-1])))
    k = _first_repeat(ids[starts]) if hard else None
    if k is not None:
        again, road = int(starts[k]), ids[starts[k]]
        first = int(starts[np.flatnonzero(ids[starts] == road)[0]])
        name = data.strings.identities[road]
        detail = (
            f"road {name!r} appears in two stretches, from edges {first} and {again}, "
            "which hard mode refuses"
        )
        return _fail("constraints", again, detail)
    return Check("constraints", True)


def _turns(data: GraphData, e_in: NDArray[np.int64], e_out: NDArray[np.int64]) -> NDArray[np.int64]:
    """Each turn's class bit (left, right, U-turn; 0 straight), from the bearings."""
    delta = (data.edge_bearing_out[e_out].astype(np.int64) - data.edge_bearing_in[e_in]) % 360
    delta = np.where(delta > 180, delta - 360, delta)  # (−180, 180], clockwise
    turned = np.zeros(len(delta), dtype=np.int64)
    turned[(delta > TURN_STRAIGHT_DEG) & (delta <= TURN_UTURN_DEG)] = TURN_RIGHT
    turned[(delta < -TURN_STRAIGHT_DEG) & (delta >= -TURN_UTURN_DEG)] = TURN_LEFT
    turned[np.abs(delta) > TURN_UTURN_DEG] = TURN_U
    return turned


def _metrics(
    data: GraphData, route: RouteLike, edges: NDArray[np.int64], node_mask: NDArray[np.bool_]
) -> Check:
    reported = route.metrics
    for name, values, value in (
        ("length_m", data.edge_length_m, reported.length_m),
        ("time_s", data.edge_time_s, reported.time_s),
    ):
        actual = math.fsum(values[edges].astype(np.float64).tolist())
        close = abs(value - actual) <= METRICS_TOLERANCE * max(1.0, abs(actual))
        if not (math.isfinite(value) and close):  # NaN compares false, so it fails here
            return Check("metrics", False, None, f"{name} reported {value}, recomputed {actual}")
    items = reported.counters.get("items")
    if items is not None:
        recounted = count_items(data, route.nodes, route.edges)
        if items != recounted:
            return Check("metrics", False, None, f"items reported {items}, recounted {recounted}")
    brand = reported.counters.get("brand")
    if brand is not None:
        brand_id = brand.get("id") if isinstance(brand, Mapping) else None
        if not (type(brand_id) is int and 0 <= brand_id < len(data.strings.brands)):  # no bool
            return Check("metrics", False, None, f"brand reported {brand}: no such brand")
        counted = brand_counts(data, brand_id, route.nodes, node_mask)
        if dict(brand) != counted:
            return Check("metrics", False, None, f"brand reported {brand}, recounted {counted}")
    letters = reported.counters.get("alphabet")
    if letters is not None:
        free_from = _free_from(letters, len(edges))
        if free_from is None:
            return Check("metrics", False, None, f"alphabet reported {letters}: no free_from")
        alpha_from, runs = _letter_runs(data, edges, free_from)
        counted = {
            "letters": len(runs),
            "sequence": "".join(letter for _, letter, _ in runs),
            "roads": [name for _, _, name in runs],
            "alpha_from": alpha_from,
            "free_from": free_from,
        }
        if not (isinstance(letters, Mapping) and dict(letters) == counted):
            return Check(
                "metrics", False, None, f"alphabet reported {letters}, recounted {counted}"
            )
    return Check("metrics", True)


def _free_from(letters: object, m: int) -> int | None:
    """The reported `free_from` when it is a position in a route of `m` edges, else None."""
    free_from = letters.get("free_from") if isinstance(letters, Mapping) else None
    if type(free_from) is not int or not 0 <= free_from <= m:  # no bool, no float
        return None
    return free_from


def _letter_runs(
    data: GraphData, edges: NDArray[np.int64], free_from: int
) -> tuple[int, list[tuple[int, str, str]]]:
    """Where the alphabet part of `edges[:free_from]` starts (its first edge of letter A, else
    `free_from`), and its runs of one letter: (first edge's position, letter, road name)."""
    part = edges[:free_from].tolist()
    lettered: list[tuple[int, str, str]] = []
    for k, e in enumerate(part):
        name_id = int(data.edge_name[e])
        if name_id < 0 or data.edge_flags[e] & EdgeFlag.LINK:
            continue
        name = data.strings.names[name_id]
        letter = road_letter(name)
        if letter is not None:
            lettered.append((k, letter, name))
    first = next((k for k, letter, _ in lettered if letter == "A"), free_from)
    runs: list[tuple[int, str, str]] = []
    for k, letter, name in lettered:
        if k >= first and (not runs or runs[-1][1] != letter):
            runs.append((k, letter, name))
    return first, runs


def _alphabet(data: GraphData, route: RouteLike, edges: NDArray[np.int64]) -> Check:
    letters = route.metrics.counters.get("alphabet")
    if letters is None:
        return Check("alphabet", False, None, "an alphabet route that reports no letters")
    free_from = _free_from(letters, len(edges))
    if free_from is None:
        return Check("alphabet", False, None, f"alphabet reported {letters}: no free_from")
    alpha_from, runs = _letter_runs(data, edges, free_from)
    for n, (k, letter, name) in enumerate(runs):
        wanted = chr(ord("A") + n % 26)
        if letter != wanted:
            detail = f"{name} ({letter}) at edge {k} where the letters want {wanted}"
            return _fail("alphabet", k, detail)
    sequence = "".join(letter for _, letter, _ in runs)
    return Check(
        "alphabet",
        True,
        None,
        f"{sequence or 'no letter'} on edges {alpha_from} to {free_from}; the first leg (before "
        f"the first A road) and the last leg (from edge {free_from}) are free: any roads",
    )
