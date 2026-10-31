"""Cleanup, between stages A and B: OSM's turn restrictions as turn tables at stage A nodes.

`docs/PLAN.md` §3.13; `docs/reference/pipeline.md`, **Turn restrictions**. `rules_of(table)` reads
the filter's restrictions table (`filter.RESTRICTION_SCHEMA`) into `Rule`s by `via` node. A rule
bans moves at its via node: from an edge arriving there whose last way is one of its `from` ways
to an edge leaving there whose first way is one of its `to` ways (a `config.RESTRICTION_NO`
value), or to any other edge leaving there (a `RESTRICTION_ONLY` value). The edges are the
simplified graph's, which keeps every junction a node. A `from` or `to` way that passes through
the via node, so that two of its edges arrive there or two leave, is matched by the turn the
value names (`Rule.turn`; left, right, straight on or a U-turn, by `graph.backend.TURN_CLASS`
over the bearings of the arriving edge's last segment and the leaving edge's first, `bearing`):
the rule concerns only the (from, to) pairs that make that turn, a `no_*` value banning those
pairs and an `only_*` value allowing, from each edge that has one, only those (the other edges
from untouched); a value that names no turn (`no_entry`, `no_exit`), or none of whose pairs
turns so, is skipped (OSRM's rule). `turn_tables` applies the rules at the stage A node holding
their via nodes (`junction_table`):

- at a plain node, its (in, out) pairs less those a rule bans;
- at a cluster, the pairs a walk from the in-edge through the edges the cluster absorbed (both
  ends among its members, read from the simplified graph) to the out-edge joins making no banned
  move at the OSM nodes it passes (`consolidate._passes`), and the pairs no walk joins at all,
  which the cluster allows as the point stage A makes of it.

A node gets its table only when a rule removes a pair. Stage B honours the tables inside the super-
junctions and keeps the others, there allowing the U-turns no route can make whatever they say
(`consolidate.stage_b`), and cleanup's no-turn rule drops a node whose table is left empty. A rule
is skipped and counted (`RestrictionStats`) when its via node lies inside an edge (simplification
contracted it: the only two roads through it are one road, so the rule bans a U-turn that cannot be
made there or mandates the only way on, unless it bans that way), when it lies on no road the
simplified graph keeps (a self-loop's, which simplification drops), or when no edge arrives at it
on a `from` way or none leaves on a `to` way (a one-way road against the turn), or when a way
passes through the via node and the turn does not tell its edges apart. The arrays engine's
`arrays.restrictions.turn_tables` gathers the same edges from arrays and calls `junction_table`
too, so the two engines make the same tables.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import networkx as nx
import numpy as np
import pyarrow as pa
import shapely

from worst_route.config import RESTRICTION_ONLY
from worst_route.graph.backend import TURN_CLASS, TURN_LEFT, TURN_RIGHT, TURN_STRAIGHT, TURN_U
from worst_route.pipeline.consolidate import _passes

type EdgeKey = tuple[int, int, int]
# An edge at a junction: (edge, the OSM node it reaches or leaves, its last or first way, the
# bearing of its segment there).
type End = tuple[Any, int, int, int]
# An edge inside a cluster: (edge, its first and last OSM nodes, its first and last ways, the
# bearings of its first and last segments).
type Inner = tuple[Any, int, int, int, int, int, int]
# The turn a restriction's value names, by its second word.
TURNS = {"left": TURN_LEFT, "right": TURN_RIGHT, "straight": TURN_STRAIGHT, "u": TURN_U}


@dataclass(frozen=True)
class Rule:
    """A turn restriction at its via node."""

    relation: int
    only: bool  # an `only_*` value: every move from the `from` ways but onto a `to` way is banned
    turn: int | None  # the turn class the value names; None for `no_entry` and `no_exit`
    from_ways: frozenset[int]
    to_ways: frozenset[int]


type Rules = dict[int, list[Rule]]  # via OSM node -> the rules there, in relation order


@dataclass
class RestrictionStats:
    """What cleanup made of the filter's turn restrictions."""

    read: int = 0  # rows of the table
    applied: int = 0  # an edge arrives at the via node on a from way and one leaves on a to way
    unmatched: int = 0  # no edge arrives on a from way, or none leaves on a to way
    way_through_via: int = 0  # a way passes through the via node and the turn does not tell
    via_inside_edge: int = 0  # simplification contracted the via node
    via_not_found: int = 0  # on no road the simplified graph keeps
    junctions: int = 0  # stage A nodes given a turn table
    pairs_banned: int = 0  # (in, out) pairs those tables leave out


def rules_of(table: pa.Table) -> Rules:
    """The table's rules by via node."""
    rules: Rules = defaultdict(list)
    for row in table.to_pylist():
        value = row["restriction"]
        rule = Rule(
            row["relation_id"],
            value in RESTRICTION_ONLY,
            TURNS.get(value.split("_")[1]),
            frozenset(row["from_ways"]),
            frozenset(row["to_ways"]),
        )
        rules[row["via_node"]].append(rule)
    return dict(rules)


def bearing(x0: float, y0: float, x1: float, y1: float) -> int:
    """Whole degrees clockwise from north, from (x0, y0) to (x1, y1), as `enrich` rounds them."""
    return int(np.rint(np.degrees(np.arctan2(x1 - x0, y1 - y0)))) % 360


def junction_table(
    ins: list[End], outs: list[End], inner: list[Inner], rules: Rules, stats: RestrictionStats
) -> set[tuple[Any, Any]] | None:
    """The allowed (in-edge, out-edge) pairs of a stage A node whose members hold the via nodes of
    `rules`, or None when they ban no pair: `ins` and `outs` its edges, `inner` the edges it
    absorbed (none at a plain node). Each rule is counted as applied or skipped in `stats`."""
    last = {(0, e): way for e, _, way, _ in ins}
    first = {(0, e): way for e, _, way, _ in outs}
    arrives = {(0, e): b for e, _, _, b in ins}
    leaves = {(0, e): b for e, _, _, b in outs}
    for e, _, _, head_way, tail_way, head_bearing, tail_bearing in inner:
        first[1, e], last[1, e] = head_way, tail_way
        leaves[1, e], arrives[1, e] = head_bearing, tail_bearing
    arriving: dict[int, list[Any]] = defaultdict(list)  # OSM node -> the edges into it
    leaving: dict[int, list[Any]] = defaultdict(list)
    for e, at, _, _ in ins:
        arriving[at].append((0, e))
    for e, at, _, _ in outs:
        leaving[at].append((0, e))
    for e, start, end, *_ in inner:
        leaving[start].append((1, e))
        arriving[end].append((1, e))
    # The rules that apply, at each via node: (only, each edge from -> the edges it concerns).
    applied: dict[int, list[tuple[bool, dict[Any, set[Any]]]]] = defaultdict(list)
    for via, at_via in rules.items():
        for rule in at_via:
            froms = [a for a in arriving[via] if last[a] in rule.from_ways]
            tos = [b for b in leaving[via] if first[b] in rule.to_ways]
            if not froms or not tos:
                stats.unmatched += 1
                continue
            resolved = _resolve(rule, froms, tos, last, first, arrives, leaves)
            if resolved is None:
                stats.way_through_via += 1
                continue
            applied[via].append((rule.only, resolved))
            stats.applied += 1
    if not applied:
        return None

    def allows(a: Any, b: Any, at: int) -> bool:
        return not any(a in moves and (b in moves[a]) != only for only, moves in applied[at])

    starts = [((0, e), at) for e, at, _, _ in ins]
    ends = [((0, e), at) for e, at, _, _ in outs]
    within = [((1, e), start, end) for e, start, end, *_ in inner]
    every = {(a, b) for a, _ in starts for b, _ in ends}
    walked = _passes(starts, ends, within, lambda a, b, at: True)
    allowed = _passes(starts, ends, within, allows) | (every - walked)
    if len(allowed) == len(every):
        return None
    stats.junctions += 1
    stats.pairs_banned += len(every) - len(allowed)
    return {(a[1], b[1]) for a, b in allowed}


def _resolve(
    rule: Rule,
    froms: list[Any],
    tos: list[Any],
    last: dict[Any, int],
    first: dict[Any, int],
    arrives: dict[Any, int],
    leaves: dict[Any, int],
) -> dict[Any, set[Any]] | None:
    """The moves a rule concerns at its via node, each edge from to the edges to it bans (a
    `no_*` value) or the only ones it allows (`only_*`): every edge from to every edge to, but
    where a way passes through the via node (two of its edges arriving, or two leaving) the pairs
    whose turn is the one the rule names, an edge from without such a pair left out; None when it
    names none or no pair turns so."""

    def through(edges: list[Any], way: dict[Any, int]) -> bool:
        return len({way[e] for e in edges}) < len(edges)

    if not through(froms, last) and not through(tos, first):
        return {a: set(tos) for a in froms}
    if rule.turn is None:
        return None
    moves = {}
    for a in froms:
        turning = {b for b in tos if TURN_CLASS[(leaves[b] - arrives[a]) % 360] == rule.turn}
        if turning:
            moves[a] = turning
    return moves or None


def _end_bearings(line: shapely.LineString) -> tuple[int, int]:
    """The bearings of a line's first and last segments."""
    (x0, y0), (x1, y1) = line.coords[:2]
    (x2, y2), (x3, y3) = line.coords[-2:]
    return bearing(x0, y0, x1, y1), bearing(x2, y2, x3, y3)


def turn_tables(
    simplified: nx.MultiDiGraph, merged: nx.MultiDiGraph, rules: Rules
) -> tuple[dict[Any, set[tuple[EdgeKey, EdgeKey]]], RestrictionStats]:
    """The turn tables `rules` give the stage A graph `merged`'s nodes (its labels, its edge
    keys), from the simplified graph it was made from, and what became of the rules."""
    stats = RestrictionStats(read=sum(map(len, rules.values())))
    holder = {m: node for node, held in merged.nodes(data="members") for m in held if m in rules}
    inside = {
        x for _, _, d in simplified.edges(data=True) for x in d["osm_nodes"][1:-1] if x in rules
    }
    at_node: dict[Any, Rules] = defaultdict(dict)
    for via, at_via in rules.items():
        if via in simplified:
            at_node[holder[via]][via] = at_via
        elif via in inside:
            stats.via_inside_edge += len(at_via)
        else:
            stats.via_not_found += len(at_via)
    tables = {}
    for node, node_rules in at_node.items():
        # A cluster's edges end at its position by a stub stage A added: the bearings leave it out.
        stub = int(merged.nodes[node]["kind"] == "cluster")
        ins = []
        for u, v, k, d in merged.in_edges(node, keys=True, data=True):
            coords = list(d["geometry"].coords)
            (x0, y0), (x1, y1) = coords[len(coords) - stub - 2 : len(coords) - stub]
            ins.append(((u, v, k), d["osm_nodes"][-1], d["osmids"][-1], bearing(x0, y0, x1, y1)))
        outs = []
        for u, v, k, d in merged.out_edges(node, keys=True, data=True):
            (x0, y0), (x1, y1) = list(d["geometry"].coords)[stub : stub + 2]
            outs.append(((u, v, k), d["osm_nodes"][0], d["osmids"][0], bearing(x0, y0, x1, y1)))
        held = set(merged.nodes[node]["members"])
        inner = [
            ((m, w, k), m, w, d["osmids"][0], d["osmids"][-1], *_end_bearings(d["geometry"]))
            for m in sorted(held)
            if m in simplified
            for _, w, k, d in simplified.out_edges(m, keys=True, data=True)
            if w in held
        ]
        table = junction_table(ins, outs, inner, node_rules, stats)
        if table is not None:
            tables[node] = table
    return tables, stats
