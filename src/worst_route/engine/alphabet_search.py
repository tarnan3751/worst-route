"""The alphabet route (`docs/PLAN.md` §3.8; `docs/reference/engine.md`, **Alphabet**): the named
roads, in order, start with A, B, C, ..., wrapping Z → A.

A road's letter is its name's (`pipeline.names.road_letter`); an unnamed edge, a link (a ramp)
and a numbered street are free connectors (`edge_letters`: 0, else 1-26 for A-Z). A route has
three parts. Its first leg, up to its first A road, is free: any roads. Its alphabet part runs
from that road's first edge (`alpha_from`, the route's first edge of letter A) to `free_from`;
along it, the letters of its lettered edges (the free ones skipped), consecutive equal letters
counting once, read A, B, C, ..., so after a road of one letter the route may drive any road of
that same letter, but the next road of another letter must carry the next one. From `free_from`
the last leg to the destination is free again. `alphabet_counts(data, edges, free_from)` reads
the alphabet part: `{letters, sequence, roads, alpha_from, free_from}` (`roads` the first road of
each letter), a route's counters under `"alphabet"`. `alphabet_scored` scores a route: letters ×
ALPHABET_LETTER_WEIGHT plus the score of the objective it is given (the distance under the
request's turn constraints: the length, less a soft constraint's penalties), so letters first,
then the longer.

Splicing a detour into an alphabet route would break the letters after it, so `alphabet_route`
is a constructive search instead of the worsen loop, on the same graph, searches, `Route.splice`
and validator. It restarts from the seed route until the budget (`max_iterations` restarts, or
the seconds, looked at between restarts only) or `ALPHABET_PATIENCE` restarts in a row without a
better route ("converged"). A restart grows its route one letter a step, each step one
`Route.splice` replacing the free last leg (from the end of the alphabet part to the
destination), so every route it makes is a whole legal route:

1. **Candidates.** One tree search (`GraphBackend.dijkstra_edges_tree`, on length) from the end
   of the alphabet part, over the edges a connector may use (any edge before the first A road;
   after it, the free ones and those of the letter just completed), with the edges of the
   wanted letter as final states, within ALPHABET_RADIUS_M of driving,
   then twice that while under `r_max`, then anywhere in the request's area. The candidates
   join each road of the wanted letter at the junction where the search first reaches it, in
   every direction it leaves that junction (both ways along a two-way road), each by its tree
   path (dropped when that path enters a node twice).
2. **Rides.** For each candidate, a tree search along its road from its first edge (that road's
   name only, avoiding the connector's nodes and twins) lists the ways on; the ride is drawn
   uniformly among the candidate's first edge alone and every simple way on (at least one edge,
   at most the whole road).
3. **Choice.** Each candidate's lookahead is the number of roads of the letter after it within
   ALPHABET_RADIUS_M of its ride's far end, on active nodes the route does not use; the
   candidates are tried in the order of keys u^(1 / (1 + lookahead)), u uniform, the highest
   first (a weighted draw without replacement), at most ALPHABET_CANDIDATE_TRIES of them.
4. **Splice.** The free last leg from the ride's end is the shortest path to the destination
   avoiding the route (`search.shortest_path`, twins avoided), tried for the drawn ride and
   then the candidate's first edge alone. The connector, the ride and that leg replace the old
   last leg when the objective's `feasible_window` agrees and `Route.splice` accepts it.

A step with no candidate, or none that splices, ends the restart; its route already ends with a
free leg to the destination, so no restart is lost. A first step with no candidate draws
nothing, so every restart would be the same: the run ends at once, "converged", on the seed.
Every search turns only where the tables and the request's turn constraints allow (the soft
ones' classes avoided where the search can); the objective's per-window bans (`banned_edges`)
and `feasible_window` apply to every step, so a constraint on the window composes. The best
route is the highest score, compared after every step; each better one is a `ProgressEvent`
whose splice replaces the whole previous best, so the events replay from the seed route to the
best. Every random draw is the given rng's, in a fixed order over candidate lists in settle
order, so a seed gives the same route on any backend.
"""

import math
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from string import ascii_uppercase
from typing import Any

import numpy as np
from numpy.typing import NDArray

from worst_route.config import (
    ALPHABET_CANDIDATE_TRIES,
    ALPHABET_LETTER_WEIGHT,
    ALPHABET_PATIENCE,
    ALPHABET_RADIUS_M,
)
from worst_route.engine.objectives.base import Objective
from worst_route.engine.route import (
    IllegalSplice,
    IntArray,
    Metrics,
    Route,
    Splice,
    TransitionForbidden,
    splice_arrays,
    twins_of,
)
from worst_route.engine.search import shortest_path
from worst_route.engine.worsen import Budget, ProgressEvent, StopReason, WorsenResult
from worst_route.graph.backend import EdgeTree, GraphBackend
from worst_route.graph.data import EdgeFlag, GraphData
from worst_route.pipeline.names import road_letter

BoolArray = NDArray[np.bool_]
LetterArray = NDArray[np.uint8]


def edge_letters(data: GraphData) -> LetterArray:
    """Each edge's letter, 1-26 for A-Z, or 0 for a free connector (unnamed, a link, a numbered
    street)."""
    by_name = [road_letter(name) for name in data.strings.names]
    of_name = np.array([0 if c is None else ord(c) - 64 for c in by_name], dtype=np.uint8)
    letters = np.zeros(data.m, dtype=np.uint8)
    named = (data.edge_name >= 0) & ((data.edge_flags & EdgeFlag.LINK) == 0)
    letters[named] = of_name[data.edge_name[named]]
    return letters


def alphabet_counts(
    data: GraphData, edges: IntArray, free_from: int, letters: LetterArray | None = None
) -> dict[str, Any]:
    """The alphabet part of a route whose last leg starts at `free_from`: `{letters, sequence,
    roads, alpha_from, free_from}`, the letters in order (equal ones in a row once), the first
    road of each, and where the part starts (its first edge of letter A, or `free_from` when it
    has none) and ends (`letters` from `edge_letters`, worked out when not given)."""
    letters = edge_letters(data) if letters is None else letters
    head = np.asarray(edges[:free_from], dtype=np.int64)
    first_a = np.flatnonzero(letters[head] == 1)
    alpha_from = int(first_a[0]) if len(first_a) else int(free_from)
    part = head[alpha_from:]
    lettered = part[letters[part] > 0]
    of = letters[lettered]
    starts = lettered[np.concatenate(([True], of[1:] != of[:-1]))] if len(of) else lettered
    return {
        "letters": len(starts),
        "sequence": "".join(ascii_uppercase[letters[e] - 1] for e in starts.tolist()),
        "roads": [data.strings.names[data.edge_name[e]] for e in starts.tolist()],
        "alpha_from": alpha_from,
        "free_from": int(free_from),
    }


def alphabet_scored(
    objective: Objective,
    data: GraphData,
    letters: LetterArray,
    nodes: IntArray,
    edges: IntArray,
    free_from: int,
) -> tuple[float, Metrics]:
    """A route's score (letters × ALPHABET_LETTER_WEIGHT + the objective's) and its metrics,
    with its alphabet counts."""
    score, metrics = objective.evaluate(nodes, edges)
    counts = alphabet_counts(data, edges, free_from, letters)
    counters = {**metrics.counters, "alphabet": counts}
    return counts["letters"] * ALPHABET_LETTER_WEIGHT + score, replace(metrics, counters=counters)


def alphabet_route(
    backend: GraphBackend,
    data: GraphData,
    route: Route,
    objective: Objective,
    budget: Budget,
    rng: np.random.Generator,
    node_mask: BoolArray,
    *,
    r_max: float,
    banned_edges: BoolArray | None = None,
    letters: LetterArray | None = None,
    on_event: Callable[[ProgressEvent], None] | None = None,
) -> WorsenResult:
    """Restart from `route` (the seed, scored by `alphabet_scored` with no letter) within the
    budget, growing each restart's route a letter a step; the best route and how the run went
    (`attempts` the restarts, `accepted` the better routes found). `letters` are the build's
    `edge_letters`, worked out when not given."""
    start = time.monotonic()
    letters = edge_letters(data) if letters is None else letters
    steps = _Steps(backend, data, objective, node_mask, banned_edges, r_max, letters)
    best = route
    events: list[ProgressEvent] = []
    refused: Counter[type[IllegalSplice]] = Counter()
    restarts = stale = 0
    stopped: StopReason = "budget"
    while restarts < budget.max_iterations and time.monotonic() - start < budget.seconds:
        restarts += 1
        current, free_from, letter = route, 0, 0
        improved = lone = False
        while True:
            window = steps.window(current, free_from, letter)
            if window is None:
                lone = letter == 0  # no A road in reach: every restart would be this one
                break
            step = steps.extend(current, window, rng, refused)
            if step is None:
                break
            current, free_from = step
            letter += 1
            if current.score > best.score:
                splice = Splice(0, len(best.edges), current.nodes, current.edges)
                elapsed = time.monotonic() - start
                event = ProgressEvent(restarts, elapsed, current.score, current.metrics, splice)
                best = current
                improved = True
                events.append(event)
                if on_event is not None:
                    on_event(event)
        stale = 0 if improved else stale + 1
        if lone or stale >= ALPHABET_PATIENCE:
            stopped = "converged"
            break
    elapsed = time.monotonic() - start
    return WorsenResult(
        best,
        tuple(events),
        restarts,
        len(events),
        stopped,
        elapsed,
        refused[TransitionForbidden],
    )


@dataclass(frozen=True, eq=False)
class _Window:
    """A step's window, its route's free last leg from position `i`: the masks its searches and
    its splice use, the letter it wants (1-26) and the candidates' (nodes, edges) onto it."""

    i: int
    want: int
    forbid: BoolArray
    bans: BoolArray
    extra: BoolArray
    candidates: list[tuple[list[int], list[int]]]


class _Steps:
    """What every step of a run shares: the searches' weights and masks, the letters, and per
    letter its edges' tails and names (the lookahead's)."""

    def __init__(
        self,
        backend: GraphBackend,
        data: GraphData,
        objective: Objective,
        node_mask: BoolArray,
        banned_edges: BoolArray | None,
        r_max: float,
        letters: LetterArray,
    ) -> None:
        self.backend, self.data, self.objective, self.active = backend, data, objective, node_mask
        self.request_banned = (
            np.zeros(data.m, dtype=np.bool_) if banned_edges is None else banned_edges
        )
        self.weights = objective.search_weights(data)
        self.letters = letters
        self.free = self.letters == 0
        self.banned_classes = objective.banned_turn_classes
        self.avoided_classes = objective.avoided_turn_classes
        self.radii: list[float] = []
        radius = ALPHABET_RADIUS_M
        while radius < r_max:
            self.radii.append(radius)
            radius *= 2
        self.radii.append(math.inf)
        self.tails: list[IntArray] = []
        self.names: list[IntArray] = []
        for letter in range(27):  # 0 unused: a free edge is no road
            of_letter = np.flatnonzero(self.letters == letter)
            self.tails.append(data.edge_u[of_letter])
            self.names.append(data.edge_name[of_letter])

    def window(self, route: Route, i: int, letter: int) -> _Window | None:
        """The window of a step adding a road of `route`'s `letter`-th letter (0 for A,
        wrapping) after its first `i` edges, with the candidates onto it; None when no road of
        that letter is in reach (nothing drawn)."""
        data, letters = self.data, self.letters
        j = len(route.edges)
        pos, end = int(route.nodes[i]), int(route.nodes[-1])
        forbid, released = route.release(data, i, j)
        forbid[end] = True  # a round trip's start too, which is its end
        forbid[pos] = False  # the searches start there (none comes back to it)
        bans = released | self.request_banned
        own = self.objective.banned_edges(data, route, i, j)  # the objective's, for the window
        extra = self.request_banned if own is None else self.request_banned | own
        if own is not None:
            bans |= own
        entry = int(route.edges[i - 1]) if i > 0 else -1
        want = letter % 26 + 1
        wanted = letters == want
        if letter == 0:  # the free first leg: any road up to the first A road
            connectors = bans
        else:  # free edges and the roads of the letter just completed
            connectors = bans | ~(self.free | wanted | (letters == (letter - 1) % 26 + 1))
        candidates = self._candidates(pos, entry, forbid, connectors, wanted)
        if not candidates:
            return None
        return _Window(i, want, forbid, bans, extra, candidates)

    def extend(
        self,
        route: Route,
        window: _Window,
        rng: np.random.Generator,
        refused: Counter[type[IllegalSplice]],
    ) -> tuple[Route, int] | None:
        """`route` with a candidate of `window` and a ride along its road spliced in, and a new
        free last leg after them: the new route and where that leg starts; None when no
        candidate tried splices."""
        i, forbid, bans, candidates = window.i, window.forbid, window.bans, window.candidates
        rides: list[tuple[list[int], list[int]]] = []
        lookahead = np.zeros(len(candidates), dtype=np.float64)
        follow = window.want % 26 + 1
        for k, (nodes, edges) in enumerate(candidates):
            ways = self._rides(nodes, edges, forbid, bans)
            ride = ways[int(rng.integers(len(ways)))]
            rides.append(ride)
            far = ride[0][-1] if ride[0] else nodes[-1]
            lookahead[k] = self._lookahead(route, follow, far)
        keys = rng.random(len(candidates)) ** (1.0 / (1.0 + lookahead))
        order = np.argsort(-keys, kind="stable")[:ALPHABET_CANDIDATE_TRIES]
        for k in order.tolist():
            nodes, edges = candidates[k]
            for ride_nodes, ride_edges in [rides[k], ([], [])] if rides[k][1] else [rides[k]]:
                done = self._splice(
                    route, i, nodes + ride_nodes, edges + ride_edges, forbid, bans, window.extra,
                    refused,
                )  # fmt: skip
                if done is not None:
                    return done, i + len(edges) + len(ride_edges)
        return None

    def _candidates(
        self,
        pos: int,
        entry: int,
        forbid: BoolArray,
        banned: BoolArray,
        wanted: BoolArray,
    ) -> list[tuple[list[int], list[int]]]:
        """The (nodes, edges) of the tree paths onto the roads of the wanted letter (`_entries`),
        from the nearest radius that finds one."""
        for radius in self.radii:
            for classes in self._classes():
                tree = self.backend.dijkstra_edges_tree(
                    pos,
                    self.weights,
                    entry_edge=entry,
                    node_mask=self.active,
                    forbid_nodes=forbid,
                    banned_edges=banned,
                    final_edges=wanted,
                    max_cost=radius,
                    banned_turn_classes=classes,
                )
                found = self._entries(tree, pos, wanted)
                if found:
                    return found
        return []

    def _classes(self) -> tuple[int, ...]:
        """The turn classes to refuse, in the order to try: the soft ones' too, first."""
        banned, avoided = self.banned_classes, self.avoided_classes
        return (banned | avoided, banned) if avoided else (banned,)

    def _entries(
        self, tree: EdgeTree, src: int, wanted: BoolArray
    ) -> list[tuple[list[int], list[int]]]:
        """The simple tree paths onto each wanted road where the tree first reaches it: to every
        state of that road leaving the junction its first settled state leaves (either way along
        a two-way road), in settle order."""
        edges = tree[0]
        names = self.data.edge_name[edges].tolist()
        tails = self.data.edge_u[edges].tolist()
        junction: dict[int, int] = {}  # each road's junction: its first settled state's tail
        found: list[tuple[list[int], list[int]]] = []
        for k in np.flatnonzero(wanted[edges]).tolist():
            if junction.setdefault(names[k], tails[k]) != tails[k]:
                continue
            path = _tree_path(self.data, tree, k, src)
            if path is not None:
                found.append(path)
        return found

    def _rides(
        self, nodes: list[int], edges: list[int], forbid: BoolArray, bans: BoolArray
    ) -> list[tuple[list[int], list[int]]]:
        """The ways on along the road of `edges[-1]` beyond it, as (nodes after its head,
        edges after it): none first, then every simple tree path, in settle order."""
        data = self.data
        first = edges[-1]
        head = nodes[-1]
        along = forbid.copy()
        along[nodes[:-1]] = True
        connector = np.array(edges, dtype=np.int32)
        banned = bans | (data.edge_name != data.edge_name[first])
        banned[connector] = True
        banned[twins_of(data, connector)[1]] = True
        tree = self.backend.dijkstra_edges_tree(
            head,
            self.weights,
            entry_edge=first,
            node_mask=self.active,
            forbid_nodes=along,
            banned_edges=banned,
            banned_turn_classes=self.banned_classes | self.avoided_classes,
        )
        ways: list[tuple[list[int], list[int]]] = [([], [])]
        for k in range(len(tree[0])):
            path = _tree_path(data, tree, k, head)
            if path is not None:
                ways.append((path[0][1:], path[1]))
        return ways

    def _lookahead(self, route: Route, letter: int, at: int) -> int:
        """How many roads of `letter` leave an active node the route does not use within
        ALPHABET_RADIUS_M of node `at`."""
        data = self.data
        tails = self.tails[letter]
        near = (
            np.hypot(data.node_x[tails] - data.node_x[at], data.node_y[tails] - data.node_y[at])
            <= ALPHABET_RADIUS_M
        )
        near &= self.active[tails] & ~route.used_nodes[tails]
        return len(np.unique(self.names[letter][near]))

    def _splice(
        self,
        route: Route,
        i: int,
        nodes: list[int],
        edges: list[int],
        forbid: BoolArray,
        bans: BoolArray,
        extra: BoolArray,
        refused: Counter[type[IllegalSplice]],
    ) -> Route | None:
        """The route with its free leg from `i` replaced by `nodes`/`edges` and a new free leg
        from their end, or None when there is no such leg or the splice is refused."""
        data = self.data
        j, end, at = len(route.edges), int(route.nodes[-1]), nodes[-1]
        ahead = forbid.copy()
        ahead[nodes] = True
        ahead[[at, end]] = False
        driven = np.array(edges, dtype=np.int32)
        banned = bans.copy()
        banned[driven] = True
        banned[twins_of(data, driven)[1]] = True
        leg = shortest_path(
            self.backend,
            at,
            end,
            self.weights,
            entry_edge=edges[-1],
            node_mask=self.active,
            forbid_nodes=ahead,
            banned_edges=banned,
            avoid_twins=True,
            banned_turn_classes=self.banned_classes,
            avoided_turn_classes=self.avoided_classes,
        )
        if leg is None:
            return None
        detour_nodes = np.array(nodes + leg[0][1:].tolist(), dtype=np.int32)
        detour_edges = np.array(edges + leg[1].tolist(), dtype=np.int32)
        if not self.objective.feasible_window(data, route, i, j, detour_nodes, detour_edges):
            return None
        new_nodes, new_edges = splice_arrays(route, i, j, detour_nodes, detour_edges)
        score, metrics = alphabet_scored(
            self.objective, data, self.letters, new_nodes, new_edges, i + len(edges)
        )
        try:
            return route.splice(
                data,
                i,
                j,
                detour_nodes,
                detour_edges,
                score=score,
                metrics=metrics,
                extra_banned=extra,
            )
        except IllegalSplice as error:
            refused[type(error)] += 1
            return None


def _tree_path(
    data: GraphData, tree: EdgeTree, k: int, src: int
) -> tuple[list[int], list[int]] | None:
    """The (nodes, edges) of the tree's path from `src` to its state `k`, or None when it enters
    a node twice."""
    edges, _, parents = tree
    path: list[int] = []
    while k >= 0:
        path.append(int(edges[k]))
        k = int(parents[k])
    path.reverse()
    nodes = [src, *data.edge_v[path].tolist()]
    if len(set(nodes)) < len(nodes):
        return None
    return nodes, path
