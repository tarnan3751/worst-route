"""The validator: proves a finished route legal from the raw data (`docs/PLAN.md` §8.1).

`validate_route(data, route, stop_nodes=, node_mask=, banned_edges=, banned_turn_classes=0)`
runs every check below
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
4. `osm_ids`: the raw-data proof. The route is expanded to original OSM node ids along its
   edges (`osmnode_ids`; consecutive edges share their junction id once); no id appears twice,
   each edge's first and last ids are members of its tail and head, and the member ids of a
   node appear in one contiguous run (a round trip's start in its first and last), so no
   consolidated junction is entered twice, whatever the engine's bookkeeping says.
5. `transitions`: every (edges[k-1], edges[k]) turn allowed at a node with transition rows.
6. `stops`: `nodes[stop_idx[s]] == stop_nodes[s]`, strictly increasing positions from the
   first node to the last.
7. `constraints`: every node in the active mask, no edge in `banned_edges`, and, under a strict
   turn constraint (`banned_turn_classes`, its class bits), no turn of a class it refuses: each
   turn classed again here from the edges' bearings (`TURN_STRAIGHT_DEG`, `TURN_UTURN_DEG`),
   not with the engine's table.
8. `metrics`: the reported length and time are finite and equal the ones recomputed from the
   edges within a relative 1e-6.

It is deliberately written without `engine/route.py`, so a bug in the engine's bookkeeping
cannot hide here too. A route whose ids are out of range fails `chain`, and the checks that
would index with them fail as skipped. `ValidationFailed` carries a failing report.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from worst_route.config import TURN_STRAIGHT_DEG, TURN_UTURN_DEG
from worst_route.graph.backend import TURN_LEFT, TURN_RIGHT, TURN_U
from worst_route.graph.data import GraphData

METRICS_TOLERANCE = 1e-6  # relative, docs/PLAN.md §8.1


class Measured(Protocol):
    """The reported numbers check 8 recomputes."""

    @property
    def length_m(self) -> float: ...

    @property
    def time_s(self) -> float: ...


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
) -> ValidationReport:
    """Run every check on `route`; see the module docstring."""
    nodes = np.asarray(route.nodes, dtype=np.int64)
    edges = np.asarray(route.edges, dtype=np.int64)
    invalid = _invalid_ids(data, nodes, edges)
    if invalid is not None:
        skipped = [
            Check(name, False, None, "skipped: the route's ids are invalid") for name in NAMES
        ]
        return ValidationReport((invalid, *skipped[1:]))
    round_trip = len(stop_nodes) > 0 and stop_nodes[0] == stop_nodes[-1]
    return ValidationReport(
        (
            _chain(data, nodes, edges, node_mask),
            _vertex_simple(nodes, round_trip),
            _edge_simple(data, edges),
            _osm_ids(data, edges, round_trip),
            _transitions(data, nodes, edges),
            _stops(nodes, route.stop_idx, stop_nodes),
            _constraints(data, nodes, edges, node_mask, banned_edges, banned_turn_classes),
            _metrics(data, edges, route.metrics),
        )
    )


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


def _osm_ids(data: GraphData, edges: NDArray[np.int64], round_trip: bool) -> Check:
    runs = [data.osmnode_ids[data.osmnode_indptr[e] : data.osmnode_indptr[e + 1]] for e in edges]
    order = np.argsort(data.member_osm_ids, kind="stable")
    members = data.member_osm_ids[order]
    holder = np.repeat(np.arange(data.n), np.diff(data.member_indptr))[order]

    def owner(ids: NDArray[np.int64]) -> NDArray[np.int64]:
        at = np.minimum(np.searchsorted(members, ids), len(members) - 1)
        found: NDArray[np.int64] = np.where(members[at] == ids, holder[at], -1)
        return found

    first = owner(data.osmnode_ids[data.osmnode_indptr[edges]])
    last = owner(data.osmnode_ids[data.osmnode_indptr[edges + 1] - 1])
    bad = np.flatnonzero((first != data.edge_u[edges]) | (last != data.edge_v[edges]))
    if len(bad):
        k = bad[0]
        return _fail(
            "osm_ids", k, f"edge {edges[k]} does not start and end at members of its nodes"
        )

    ids_parts: list[NDArray[np.int64]] = []
    edge_parts: list[NDArray[np.int64]] = []
    for k, run in enumerate(runs):
        if k and run[0] == ids_parts[-1][-1]:
            run = run[1:]  # the junction id the previous edge ended on
        ids_parts.append(run)
        edge_parts.append(np.full(len(run), k, dtype=np.int64))
    ids = np.concatenate(ids_parts)
    edge_at = np.concatenate(edge_parts)
    held = owner(ids)
    closing = round_trip and held[0] == held[-1]

    k = _first_repeat(ids[:-1] if closing and ids[0] == ids[-1] else ids)
    if k is not None:
        return _fail("osm_ids", edge_at[k], f"original OSM node {ids[k]} passed twice")

    starts = np.flatnonzero(np.concatenate(([True], held[1:] != held[:-1])))
    run_owner = held[starts]
    junction = run_owner >= 0
    run_owner, starts = run_owner[junction], starts[junction]
    if closing:
        run_owner, starts = run_owner[:-1], starts[:-1]  # the start's run at the end
    k = _first_repeat(run_owner)
    if k is not None:
        node = run_owner[k]
        return _fail("osm_ids", edge_at[starts[k]], f"the members of node {node} entered twice")
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


def _metrics(data: GraphData, edges: NDArray[np.int64], reported: Measured) -> Check:
    for name, values, value in (
        ("length_m", data.edge_length_m, reported.length_m),
        ("time_s", data.edge_time_s, reported.time_s),
    ):
        actual = math.fsum(values[edges].astype(np.float64).tolist())
        close = abs(value - actual) <= METRICS_TOLERANCE * max(1.0, abs(actual))
        if not (math.isfinite(value) and close):  # NaN compares false, so it fails here
            return Check("metrics", False, None, f"{name} reported {value}, recomputed {actual}")
    return Check("metrics", True)
