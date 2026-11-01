"""A route drawn by hand, one edge at a time (`docs/reference/engine.md`, **Drawing**).

A drawing is a path of edges from a request's start, which a player extends a move at a time
under the engine's rules: no node twice, no edge twice and no edge with its twin, the one-ways
(an edge only leaves its tail), the transition tables, the request's area and bans, the stops
in order, a round trip's start only as its end, and the request's strict turn constraint and
hard mode (a soft constraint changes nothing here: it only scores). It is checked whole on
every call, from the path alone.

`DrawRules` is what a drawing keeps to: the resolved stop nodes (`resolve_stops`), the request's
active nodes and banned edges (`request_masks`), and its refused turn classes and hard mode.
`check_drawing(data, rules, path)` checks a path as the beginning of a route and returns a
`Drawing` (its nodes, its edges, the positions of the stops reached in order, whether the end is
reached), or raises `DrawRefused(code, index)`, where `index` is the first edge of the path that
is not a legal move: `path[:index]` is legal. The codes:

- `BROKEN_CHAIN`: an id outside the graph, an edge that does not leave the node the path is at
  (a one-way's only edge taken from its head, say), or a self-loop;
- `OUTSIDE_AREA`: an edge into a node outside the active mask;
- `NODE_REUSED`, `ROUND_TRIP_START_REUSED`: a node the path has passed (a round trip's start
  but as its end, once every stop between was reached);
- `EDGE_BANNED`: an edge the request bans, the twin of one the path drives, or a two-edge round
  trip out and back along one road;
- `TRANSITION_FORBIDDEN`: a turn the node's transition rows do not list;
- `STOP_SKIPPED`: a stop reached before the one before it (the end included);
- `PAST_END`: an edge after the end;
- `TURN_REFUSED`: a turn of a class the strict turn constraint refuses;
- `ROAD_AGAIN`: under hard mode, a road the path left and comes back to.

The route rules are `route.check_route`'s, the one place they are written: the path is checked
as a whole route whose stops are those reached so far and, when its last node is not one of
them, that node (`check_route` reads its stop positions for their shape only, so this stands-in
stop masks no rule); the stops' order, the end and the request's own rules (area, strict turns,
hard mode, as `plan.check_base` reads them) are checked beside it. Each check names the first
position it fails at, `check_route` the first of the first rule it finds broken; legality is
closed under prefixes, so the earliest of them is checked again on the path before it until
that path passes, which makes it the first illegal edge.

`next_moves(data, rules, drawing)` is the out-edges of the drawing's last node that extend it
legally, filtered from the masks, the path's nodes, edges and twins, the transition rows, the
stops and the constraints, without checking a route per candidate: exactly the edges e for which
`check_drawing` passes `path + [e]` (`tests/engine/test_draw.py` holds it on random walks).

`finish_drawing(data, request, rules, snaps, baseline, path, r_max=...)` makes a finished
drawing a route: `Route.new` on it, scored by the distance objective with the request's
constraints as `plan_route` scores a seed, then proven by `validate_route` on the real stops,
and returns its `route.json` document (`io.document`), whose run is `{attempts, accepted}` its
moves, `stopped_because: "drawn"`, `elapsed_s: 0` and `backend: "human"`. A drawing that has not
reached the end raises `DrawingUnfinished`; a route the validator refuses, `ValidationFailed`.
`drawing_problem(objective, target_kind)` is what a drawing's request may not ask, as (the
field, a sentence): a drawing is the longest route, as bad as the player makes it, so the
objective `distance` and the target `max` only.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from worst_route.engine.constraints import Constraints, constrain, returning
from worst_route.engine.io import document
from worst_route.engine.objectives import make_objective
from worst_route.engine.request import RequestMasks, RouteRequest
from worst_route.engine.route import (
    BoolArray,
    BrokenChain,
    EdgeBanned,
    IllegalSplice,
    IntArray,
    NodeReused,
    RoundTripStartReused,
    Route,
    TransitionForbidden,
    check_route,
    twins_of,
)
from worst_route.engine.seed_route import Baseline
from worst_route.engine.turns import turn_classes
from worst_route.engine.validator import ValidationFailed, validate_route
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import Snap

_ROUTE_CODES: dict[type[IllegalSplice], str] = {
    BrokenChain: "BROKEN_CHAIN",
    NodeReused: "NODE_REUSED",
    RoundTripStartReused: "ROUND_TRIP_START_REUSED",
    EdgeBanned: "EDGE_BANNED",
    TransitionForbidden: "TRANSITION_FORBIDDEN",
}
_AT_NODE = (NodeReused, RoundTripStartReused)  # their index is a node's position


@dataclass(frozen=True, eq=False)
class DrawRules:
    """What a drawing on one request keeps to: its stop nodes, active nodes and banned edges, and
    the turn classes its strict constraint refuses and its hard mode."""

    stop_nodes: tuple[int, ...]
    node_mask: BoolArray
    banned_edges: BoolArray
    banned_turn_classes: int = 0
    hard: bool = False

    @classmethod
    def of(
        cls, masks: RequestMasks, stop_nodes: Sequence[int], constraints: Constraints
    ) -> "DrawRules":
        """The rules of a request with these masks, resolved stops and constraints."""
        return cls(
            tuple(int(v) for v in stop_nodes),
            masks.active,
            masks.banned,
            constraints.banned_turn_classes,
            constraints.hard,
        )

    @property
    def round_trip(self) -> bool:
        """Whether the drawing ends where it starts."""
        return self.stop_nodes[0] == self.stop_nodes[-1]


@dataclass(frozen=True, eq=False)
class Drawing:
    """A legal path from the start: its nodes and edges, the positions in `nodes` of the stops
    reached (in order, the start's 0 first), and whether it has reached the end."""

    nodes: IntArray
    edges: IntArray
    reached: tuple[int, ...]
    done: bool

    @property
    def at(self) -> int:
        """The node the drawing is at."""
        return int(self.nodes[-1])


class DrawRefused(Exception):  # noqa: N818 (a refusal, as `IllegalSplice` is)
    """A path that is not a legal drawing: `code` says why, `index` is its first illegal edge."""

    def __init__(self, code: str, index: int, message: str) -> None:
        super().__init__(f"{message} (edge {index} of the path)")
        self.code = code
        self.index = index


class DrawingUnfinished(ValueError):  # noqa: N818 (reads as the condition)
    """A legal drawing that has not reached the end; `reached` counts the stops it has."""

    def __init__(self, reached: int, stops: int) -> None:
        super().__init__(f"the drawing has not reached the end: {reached} of {stops} stops reached")
        self.reached = reached


def drawing_problem(objective: str, target_kind: str) -> tuple[str, str] | None:
    """What a drawing's request may not ask, as (the field, a sentence), or None."""
    if objective != "distance":
        return "objective", "a drawing is the longest route: objective distance only"
    if target_kind != "max":
        return "target", "a drawing takes no target: it is as bad as you make it"
    return None


def check_drawing(data: GraphData, rules: DrawRules, path: Sequence[int]) -> Drawing:
    """The drawing `path` is, or `DrawRefused` at its first illegal edge."""
    edges = np.asarray(path, dtype=np.int64).reshape(-1)
    refused: DrawRefused | None = None
    end = len(edges)
    while True:
        drawing, found = _walk(data, rules, edges[:end])
        if found is None:
            break
        refused, end = found, found.index
    if refused is not None:  # every check of the path to its first illegal edge fails there
        raise _walk(data, rules, edges[: refused.index + 1])[1] or refused
    assert drawing is not None
    return drawing


def next_moves(data: GraphData, rules: DrawRules, drawing: Drawing) -> IntArray:
    """The out-edges of the drawing's last node that extend it legally, ascending."""
    if drawing.done:
        return np.zeros(0, dtype=np.int32)
    at, edges = drawing.at, drawing.edges
    out = data.out_edges[data.out_indptr[at] : data.out_indptr[at + 1]]
    heads = data.edge_v[out]
    keep = ~rules.banned_edges[out] & rules.node_mask[heads] & (heads != at)
    stops = np.asarray(rules.stop_nodes)
    wanted = int(stops[len(drawing.reached)])  # the stop due next
    # a node passed, but a round trip's start as its end (once every stop between is reached),
    # and never as a two-edge round trip out and back along one road
    closing = (heads == wanted) & (wanted == stops[0])
    if len(edges) == 1:
        closing &= data.edge_unit[out] != data.edge_unit[edges[0]]
    keep &= ~np.isin(heads, drawing.nodes) | closing
    keep &= ~np.isin(heads, stops) | (heads == wanted)
    if len(edges):
        keep &= ~np.isin(out, np.concatenate((edges, twins_of(data, edges)[1])))
        last = edges[-1]
        lo, hi = np.searchsorted(data.trans_node, [at, at + 1])
        if hi > lo:
            rows = slice(int(lo), int(hi))
            keep &= np.isin(out, data.trans_out[rows][data.trans_in[rows] == last])
        if rules.banned_turn_classes:
            classes = turn_classes(data, np.full(len(out), last, dtype=np.int32), out)
            keep &= (classes & rules.banned_turn_classes) == 0
        if rules.hard:
            ids, identity = data.edge_identity[edges], data.edge_identity[out]
            keep &= (identity < 0) | (identity == ids[-1]) | ~np.isin(identity, ids)
    moves: IntArray = out[keep].astype(np.int32)
    return moves


def finish_drawing(
    data: GraphData,
    request: RouteRequest,
    rules: DrawRules,
    snaps: Sequence[Snap],
    baseline: Baseline,
    path: Sequence[int],
    *,
    r_max: float,
) -> dict[str, Any]:
    """The `route.json` document of the finished drawing `path`, validated."""
    drawing = check_drawing(data, rules, path)
    if not drawing.done:
        raise DrawingUnfinished(len(drawing.reached), len(rules.stop_nodes))
    objective = constrain(
        make_objective("distance", data, baseline=baseline.metrics, r_max=r_max),
        request.constraints,
        data,
    )
    score, metrics = objective.evaluate(drawing.nodes, drawing.edges)
    route = Route.new(
        data,
        drawing.nodes,
        drawing.edges,
        drawing.reached,
        score=score,
        metrics=metrics,
        seed=request.seed,
        extra_banned=rules.banned_edges,
    )
    report = validate_route(
        data,
        route,
        stop_nodes=rules.stop_nodes,
        node_mask=rules.node_mask,
        banned_edges=rules.banned_edges,
        banned_turn_classes=rules.banned_turn_classes,
        hard=rules.hard,
    )
    if not report.ok:
        raise ValidationFailed(report)
    moves = len(drawing.edges)
    run = {
        "attempts": moves,
        "accepted": moves,
        "stopped_because": "drawn",
        "elapsed_s": 0.0,
        "backend": "human",
    }
    return document(data, request, snaps, route, baseline, run=run)


def _walk(
    data: GraphData, rules: DrawRules, edges: NDArray[np.int64]
) -> tuple[Drawing | None, DrawRefused | None]:
    """The drawing `edges` make, or one refusal of it (the earliest each check finds)."""
    start = rules.stop_nodes[0]
    outside = np.flatnonzero((edges < 0) | (edges >= data.m))
    if len(outside):
        k = int(outside[0])
        return None, DrawRefused("BROKEN_CHAIN", k, f"edge {edges[k]} is not in the graph")
    e = edges.astype(np.int32)
    nodes = np.concatenate(([start], data.edge_v[e])).astype(np.int32)
    broken = np.flatnonzero(data.edge_u[e] != nodes[:-1])
    if len(broken):
        k = int(broken[0])
        message = f"edge {e[k]} does not leave node {nodes[k]}"
        return None, DrawRefused("BROKEN_CHAIN", k, message)

    found: list[DrawRefused] = []
    reached, done = _stops(rules, nodes, found)
    if len(e):
        last = len(nodes) - 1
        stop_idx = reached if reached[-1] == last else [*reached, last]
        try:
            check_route(data, nodes, e, stop_idx, rules.banned_edges)
        except IllegalSplice as error:
            k = error.index
            # a node passed again is the edge into it, but a round trip's start, which comes back
            # as its end: there the stops' walk judges it, and the route goes wrong on leaving it
            if isinstance(error, _AT_NODE) and not (rules.round_trip and nodes[k] == start):
                k -= 1
            found.append(DrawRefused(_ROUTE_CODES[type(error)], k, _said(error)))
    away = np.flatnonzero(~rules.node_mask[nodes[1:]])
    if len(away):
        k = int(away[0])
        found.append(DrawRefused("OUTSIDE_AREA", k, f"node {nodes[k + 1]} is outside the area"))
    if rules.banned_turn_classes and len(e) > 1:
        refused = np.flatnonzero(turn_classes(data, e[:-1], e[1:]) & rules.banned_turn_classes)
        if len(refused):
            k = int(refused[0]) + 1
            message = f"the turn into edge {e[k]} at node {nodes[k]} is refused"
            found.append(DrawRefused("TURN_REFUSED", k, message))
    again = returning(data, e) if rules.hard else None
    if again is not None:
        k = again[1][0]
        road = data.strings.identities[data.edge_identity[e[k]]]
        found.append(DrawRefused("ROAD_AGAIN", k, f"it comes back to {road}, which it left"))
    if found:
        return None, min(found, key=lambda r: r.index)
    for a in (nodes, e):
        a.flags.writeable = False
    return Drawing(nodes, e, tuple(reached), done), None


def _stops(rules: DrawRules, nodes: IntArray, found: list[DrawRefused]) -> tuple[list[int], bool]:
    """The positions of the stops `nodes` reaches in order and whether the last is the end;
    the first stop out of order, revisit of one or edge past the end goes into `found`."""
    stops = rules.stop_nodes
    reached = [0]
    for p in (np.flatnonzero(np.isin(nodes[1:], stops)) + 1).tolist():
        v = int(nodes[p])
        if v == stops[len(reached)]:
            reached.append(p)
            if len(reached) == len(stops):
                if p < len(nodes) - 1:
                    found.append(DrawRefused("PAST_END", p, "the drawing goes on past the end"))
                return reached, True
            continue
        if v == stops[0]:
            code = "ROUND_TRIP_START_REUSED" if rules.round_trip else "NODE_REUSED"
            found.append(DrawRefused(code, p - 1, f"node {v}, the start, is passed again"))
        elif v in stops[len(reached) + 1 :]:
            k = stops.index(v, len(reached) + 1)
            message = f"stop {k} is reached before stop {len(reached)}"
            found.append(DrawRefused("STOP_SKIPPED", p - 1, message))
        else:
            found.append(DrawRefused("NODE_REUSED", p - 1, f"stop node {v} is passed again"))
        break
    return reached, False


def _said(error: IllegalSplice) -> str:
    """What a route refusal says, without its position in the route (the drawing's is given)."""
    message = str(error.args[0])
    return message.rsplit(" (route position ", 1)[0]
