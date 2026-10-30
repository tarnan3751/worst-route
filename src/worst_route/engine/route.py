"""`Route`, its only mutation `splice()`, and the refusals (`docs/reference/engine.md`, **Routes**).

A `Route` is one legal route on one build: `nodes` (each node once, except that a round trip's
start is also its end), `edges` (`edges[k]` goes `nodes[k]` → `nodes[k + 1]`), `stop_idx` (the
stops' positions in `nodes`: strictly increasing, the first 0 and the last `len(nodes) - 1`),
`used_nodes` (bool[n]) and `banned_edges` (bool[m]: the route's edges and their twins),
`ban_count` (int32[m]: how many of the route's edges ban each edge, as itself or as a twin), the
score and metrics its objective gave it, the seed, and the build's `graph_version`. Its arrays
are read-only. A request's and an objective's bans are not the route's: the caller passes them
per window as `extra_banned`.

`Route.new` checks a whole route and makes it; `Route.splice(data, i, j, nodes, edges, ...)`
replaces the interior of `nodes[i..j]` (i < j) by a detour from `nodes[i]` to `nodes[j]` and
returns a new route. Both refuse, with a subclass of `IllegalSplice` carrying the position at
fault in the resulting route:

- `EndpointMismatch`: the detour does not start at `nodes[i]` and end at `nodes[j]` (it needs
  two nodes at least);
- `BrokenChain`: its edges do not chain from node to node, or one is a self-loop;
- `StopInterior`: a stop lies strictly inside the removed run;
- `NodeReused`: it enters a node the route still uses once the removed interior is released,
  or one node twice (`RoundTripStartReused` when that node is a round trip's start);
- `EdgeBanned`: it uses an edge still banned once the removed edges' own bans are released,
  an edge of `extra_banned`, the twin of another of its edges, or both directions of one road
  in a two-edge round trip (the only way a vertex-simple route can drive a road twice);
- `TransitionForbidden`: at some junction of the window, the two boundary junctions included,
  the node has transition rows and none allows (in_edge, out_edge).

Releasing a run is exact: an edge the removed edges banned stays banned iff it is a kept edge
or the twin of one (twins are symmetric), that is iff its ban count less the removed edges'
bans is positive, which costs the window, not the route. `release(data, i, j)` gives the search
masks for a
detour of `nodes[i..j]`: the used nodes without the run (its two ends cleared, so a search may
start and end there) and the bans that remain. The score and metrics come from the caller;
the route does not know its objective.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from worst_route.graph.data import GraphData

IntArray = NDArray[np.int32]
BoolArray = NDArray[np.bool_]


@dataclass(frozen=True)
class Metrics:
    """What a route measures: length, driving time, the objective's own counters (JSON values:
    the collect mode's are its item and the route's count of every item), and the delays at its
    nodes (signals and stop signs; a reader that needs only the first two may leave them at 0)."""

    length_m: float
    time_s: float
    counters: Mapping[str, Any] = field(default_factory=dict)
    delay_s: float = field(default=0.0, kw_only=True)


@dataclass(frozen=True, eq=False)
class Splice:
    """One accepted change: the interior of `nodes[i..j]` replaced by `nodes`, `edges`."""

    i: int
    j: int
    nodes: IntArray
    edges: IntArray


class IllegalSplice(Exception):  # noqa: N818 (the name the design uses)
    """A route or a splice that would break the no-repeat rule; `index` is the position."""

    def __init__(self, index: int, message: str) -> None:
        super().__init__(f"{message} (route position {index})")
        self.index = index


class EndpointMismatch(IllegalSplice):
    """The detour does not run from `nodes[i]` to `nodes[j]`."""


class BrokenChain(IllegalSplice):
    """The edges do not chain from node to node, or one is a self-loop."""


class StopInterior(IllegalSplice):
    """The removed run has a stop strictly inside it."""


class NodeReused(IllegalSplice):
    """A node the route already uses, or one node twice."""


class RoundTripStartReused(NodeReused):
    """A round trip's start anywhere but at its two ends."""


class EdgeBanned(IllegalSplice):
    """An edge used, the twin of one used, banned by the caller, or a road driven twice."""


class TransitionForbidden(IllegalSplice):
    """A turn from one edge to the next that the node's transition table does not allow."""


def twins_of(data: GraphData, edges: IntArray) -> tuple[NDArray[np.intp], IntArray]:
    """Every twin of every edge in `edges`, as (index into `edges`, twin edge id)."""
    starts = data.twin_indptr[edges]
    counts = data.twin_indptr[edges + 1] - starts
    owner = np.repeat(np.arange(len(edges)), counts)
    offset = np.repeat(starts - (np.cumsum(counts) - counts), counts)
    return owner, data.twin_edges[offset + np.arange(len(owner))]


def check_route(
    data: GraphData,
    nodes: IntArray,
    edges: IntArray,
    stop_idx: Sequence[int],
    extra_banned: BoolArray | None = None,
) -> None:
    """Raise the `IllegalSplice` a whole route breaks first; return if it is legal."""
    stops = np.asarray(stop_idx)
    if (
        len(stops) < 2
        or stops[0] != 0
        or stops[-1] != len(nodes) - 1
        or (np.diff(stops) <= 0).any()
    ):
        raise ValueError(
            f"stop_idx {list(stop_idx)}: must rise strictly from 0 to {len(nodes) - 1}"
        )
    _check_chain(data, nodes, edges, 0)
    round_trip = bool(nodes[0] == nodes[-1])
    inner = nodes[1:-1]
    if round_trip:
        at_start = np.flatnonzero(inner == nodes[0])
        if len(at_start):
            raise RoundTripStartReused(
                int(at_start[0]) + 1, f"round trip start {nodes[0]} revisited"
            )
    _check_unique(nodes[:-1] if round_trip else nodes, 0)
    _check_edges(data, edges, np.zeros(len(edges), dtype=np.bool_), extra_banned, 0)
    _check_road_twice(data, nodes, edges)
    _check_transitions(data, edges[:-1], edges[1:], 1)


@dataclass(frozen=True, eq=False)
class Route:
    """A legal route on one build; `splice()` is its only mutation."""

    nodes: IntArray
    edges: IntArray
    stop_idx: tuple[int, ...]
    used_nodes: BoolArray
    banned_edges: BoolArray
    ban_count: IntArray
    score: float
    metrics: Metrics
    seed: int
    graph_version: str

    @classmethod
    def new(
        cls,
        data: GraphData,
        nodes: IntArray,
        edges: IntArray,
        stop_idx: Sequence[int],
        *,
        score: float,
        metrics: Metrics,
        seed: int,
        extra_banned: BoolArray | None = None,
    ) -> "Route":
        """Check a whole route (`check_route`) and make it."""
        nodes, edges = _int32(nodes), _int32(edges)
        check_route(data, nodes, edges, stop_idx, extra_banned)
        return cls._made(data, nodes, edges, tuple(int(s) for s in stop_idx), score, metrics, seed)

    @property
    def is_round_trip(self) -> bool:
        """Whether the route ends where it starts."""
        return bool(self.nodes[0] == self.nodes[-1])

    def release(self, data: GraphData, i: int, j: int) -> tuple[BoolArray, BoolArray]:
        """The (forbid_nodes, banned_edges) masks for a search replacing `nodes[i..j]`."""
        self._check_window(data, i, j)
        forbid = self.used_nodes.copy()
        forbid[self.nodes[i : j + 1]] = False
        banned = self.banned_edges.copy()
        released, left = self._released(data, i, j)
        banned[released] = left > 0
        return forbid, banned

    def check_splice(
        self,
        data: GraphData,
        i: int,
        j: int,
        nodes: IntArray,
        edges: IntArray,
        extra_banned: BoolArray | None = None,
    ) -> None:
        """Raise the `IllegalSplice` replacing `nodes[i..j]` by the detour breaks first."""
        self._check_window(data, i, j)
        nodes, edges = _int32(nodes), _int32(edges)
        if len(nodes) < 2 or nodes[0] != self.nodes[i] or nodes[-1] != self.nodes[j]:
            raise EndpointMismatch(
                i,
                f"detour must run {self.nodes[i]} → {self.nodes[j]}, got {nodes[:1]}…{nodes[-1:]}",
            )
        _check_chain(data, nodes, edges, i)
        inside = [s for s in self.stop_idx if i < s < j]
        if inside:
            raise StopInterior(inside[0], f"stop at position {inside[0]} inside the removed run")

        inner = nodes[1:-1]
        taken = self.used_nodes[inner] & ~np.isin(inner, self.nodes[i + 1 : j])
        if taken.any():
            k = int(np.flatnonzero(taken)[0])
            v = int(inner[k])
            if self.is_round_trip and v == self.nodes[0]:
                raise RoundTripStartReused(i + 1 + k, f"round trip start {v} revisited")
            raise NodeReused(i + 1 + k, f"node {v} is already on the route")
        _check_unique(nodes[:-1] if nodes[0] == nodes[-1] else nodes, i)

        held = self.banned_edges[edges]
        if held.any():
            released, left = self._released(data, i, j)
            at = np.minimum(np.searchsorted(released, edges), len(released) - 1)
            held &= (released[at] != edges) | (left[at] > 0)
        _check_edges(data, edges, held, extra_banned, i)
        if len(self.edges) - (j - i) + len(edges) == 2 and self.is_round_trip:
            route_nodes, route_edges = splice_arrays(self, i, j, nodes, edges)
            _check_road_twice(data, route_nodes, route_edges)

        e_in = edges[:-1]
        e_out = edges[1:]
        first = i + 1
        if i > 0:
            e_in = np.concatenate(([self.edges[i - 1]], e_in))
            e_out = np.concatenate(([edges[0]], e_out))
            first = i
        if j < len(self.edges):
            e_in = np.concatenate((e_in, [edges[-1]]))
            e_out = np.concatenate((e_out, [self.edges[j]]))
        _check_transitions(data, _int32(e_in), _int32(e_out), first)

    def splice(
        self,
        data: GraphData,
        i: int,
        j: int,
        nodes: IntArray,
        edges: IntArray,
        *,
        score: float,
        metrics: Metrics,
        extra_banned: BoolArray | None = None,
    ) -> "Route":
        """The route with the interior of `nodes[i..j]` replaced; refuses what is illegal."""
        nodes, edges = _int32(nodes), _int32(edges)
        self.check_splice(data, i, j, nodes, edges, extra_banned)
        new_nodes, new_edges = splice_arrays(self, i, j, nodes, edges)
        shift = len(nodes) - (j - i + 1)
        stops = tuple(s if s <= i else s + shift for s in self.stop_idx)
        return self._made(data, new_nodes, new_edges, stops, score, metrics, self.seed)

    @classmethod
    def _made(
        cls,
        data: GraphData,
        nodes: IntArray,
        edges: IntArray,
        stop_idx: tuple[int, ...],
        score: float,
        metrics: Metrics,
        seed: int,
    ) -> "Route":
        used = np.zeros(data.n, dtype=np.bool_)
        used[nodes] = True
        bans = np.concatenate((edges, twins_of(data, edges)[1]))
        count = np.bincount(bans, minlength=data.m).astype(np.int32)
        banned = count > 0
        for a in (nodes, edges, used, banned, count):
            a.flags.writeable = False
        return cls(
            nodes=nodes,
            edges=edges,
            stop_idx=stop_idx,
            used_nodes=used,
            banned_edges=banned,
            ban_count=count,
            score=float(score),
            metrics=metrics,
            seed=seed,
            graph_version=str(data.meta["graph_version"]),
        )

    def _check_window(self, data: GraphData, i: int, j: int) -> None:
        if data.meta["graph_version"] != self.graph_version:
            raise ValueError("the route was made on another build")
        if not 0 <= i < j <= len(self.edges):
            raise ValueError(f"window ({i}, {j}): need 0 <= i < j <= {len(self.edges)}")

    def _released(self, data: GraphData, i: int, j: int) -> tuple[IntArray, IntArray]:
        """The edges the removed run `edges[i:j]` bans (sorted, each once) and how many of the
        kept edges still ban each of them."""
        removed = self.edges[i:j]
        bans = np.concatenate((removed, twins_of(data, removed)[1]))
        released, dropped = np.unique(bans, return_counts=True)
        left: IntArray = self.ban_count[released] - dropped.astype(np.int32)
        return released.astype(np.int32), left


def splice_arrays(
    route: Route, i: int, j: int, nodes: IntArray, edges: IntArray
) -> tuple[IntArray, IntArray]:
    """The node and edge arrays `route.splice(i, j, nodes, edges)` would have; no checks."""
    new_nodes = np.concatenate((route.nodes[:i], nodes, route.nodes[j + 1 :]))
    new_edges = np.concatenate((route.edges[:i], edges, route.edges[j:]))
    return _int32(new_nodes), _int32(new_edges)


def _int32(a: NDArray[Any] | Sequence[int]) -> IntArray:
    return np.ascontiguousarray(a, dtype=np.int32).reshape(-1)


def _check_chain(data: GraphData, nodes: IntArray, edges: IntArray, base: int) -> None:
    if len(edges) == 0 or len(edges) != len(nodes) - 1:
        raise BrokenChain(
            base,
            f"need one edge or more, one fewer than nodes: {len(nodes)} nodes, {len(edges)} edges",
        )
    bad = (nodes < 0) | (nodes >= data.n)
    if bad.any():
        k = int(np.flatnonzero(bad)[0])
        raise BrokenChain(base + k, f"node {nodes[k]} is not in the graph")
    bad = (edges < 0) | (edges >= data.m)
    if bad.any():
        k = int(np.flatnonzero(bad)[0])
        raise BrokenChain(base + k, f"edge {edges[k]} is not in the graph")
    bad = (data.edge_u[edges] != nodes[:-1]) | (data.edge_v[edges] != nodes[1:])
    if bad.any():
        k = int(np.flatnonzero(bad)[0])
        raise BrokenChain(base + k, f"edge {edges[k]} does not run {nodes[k]} → {nodes[k + 1]}")
    loops = data.edge_u[edges] == data.edge_v[edges]
    if loops.any():
        k = int(np.flatnonzero(loops)[0])
        raise BrokenChain(base + k, f"edge {edges[k]} is a self-loop")


def _check_unique(nodes: IntArray, base: int) -> None:
    """Refuse the first position whose node came earlier."""
    first = np.zeros(len(nodes), dtype=np.bool_)
    first[np.unique(nodes, return_index=True)[1]] = True
    if not first.all():
        k = int(np.flatnonzero(~first)[0])
        raise NodeReused(base + k, f"node {nodes[k]} twice")


def _check_edges(
    data: GraphData,
    edges: IntArray,
    held: BoolArray,
    extra_banned: BoolArray | None,
    base: int,
) -> None:
    """Refuse an edge held by the route, banned by the caller, or the twin of an earlier one."""
    banned = held.copy()
    if extra_banned is not None:
        banned |= extra_banned[edges]
    owner, twins = twins_of(data, edges)
    position = {int(e): k for k, e in enumerate(edges)}
    for k, t in zip(owner.tolist(), twins.tolist(), strict=True):
        other = position.get(t)
        if other is not None:
            banned[max(k, other)] = True
    if banned.any():
        k = int(np.flatnonzero(banned)[0])
        raise EdgeBanned(base + k, f"edge {edges[k]} is used, a twin of one used, or banned")


def _check_road_twice(data: GraphData, nodes: IntArray, edges: IntArray) -> None:
    """A two-edge round trip must not drive one road out and back."""
    if (
        len(edges) == 2
        and nodes[0] == nodes[-1]
        and data.edge_unit[edges[0]] == data.edge_unit[edges[1]]
    ):
        raise EdgeBanned(1, f"edges {edges[0]} and {edges[1]} are one road out and back")


def _check_transitions(data: GraphData, e_in: IntArray, e_out: IntArray, first: int) -> None:
    """Refuse the first (e_in[k], e_out[k]) turn the transition table of its node forbids."""
    if len(e_in) == 0 or len(data.trans_node) == 0:
        return
    at = data.edge_v[e_in]
    lo = np.searchsorted(data.trans_node, at, side="left")
    hi = np.searchsorted(data.trans_node, at, side="right")
    m = np.int64(data.m)
    for k in np.flatnonzero(hi > lo).tolist():
        rows = slice(int(lo[k]), int(hi[k]))
        keys = data.trans_in[rows].astype(np.int64) * m + data.trans_out[rows]
        key = np.int64(e_in[k]) * m + e_out[k]
        pos = int(np.searchsorted(keys, key))
        if pos == len(keys) or keys[pos] != key:
            raise TransitionForbidden(
                first + k, f"turn {e_in[k]} → {e_out[k]} not allowed at node {at[k]}"
            )
