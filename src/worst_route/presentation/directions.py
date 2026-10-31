"""Turn-by-turn directions (`docs/reference/presentation.md`, **Directions**).

`directions(data, nodes, edges, stop_idx, *, labels)` turns a route into GPS-style `Step`s.
Each step has its number, its text, its distance and time, its `edge_range` (half-open,
`[a, b)` over the route's edge positions) and its `kind`. The steps that drive (`depart`,
`continue`, `turn`, `ramp`, `roundabout`) have non-empty ranges that tile `[0, len(edges))` in
order; `stop` and `arrive` steps are empty, `[k, k)`, at their position. A step's distance is
its edges' `edge_length_m`; its time is their `edge_time_s` and the delays (`node_delay_s`) of
the nodes it arrives at, the first step's start node included (a round trip's once), so the
steps' times add up to the route's driving time and delays.

The edges are cut into steps at junctions, in order of these rules, at node v between edges e
and f:

1. v is a stop between the ends: a `stop` step, "Arrive at <label or 'stop N'>; continue",
   then a new step from v by the rules below; where they would go on in the same step, it is
   `continue` "Continue on X".
2. v is a roundabout: `roundabout` "At the roundabout, take the exit onto X", even on the same
   road.
3. f is a link (`EdgeFlag.LINK`) and e is not: `ramp` "Take the ramp toward X", X the first
   step after it that does not start on a link.
4. e and f are both links: the same step (a ramp's curves and forks are the ramp's).
5. A left or right turn or a U-turn (`engine.turns.turn_classes`), on the same road or not,
   off a ramp too: `turn`, "Turn left onto X", "Slight left" within `DIRECTIONS_SLIGHT_DEG` of
   straight on, "Sharp left" beyond `DIRECTIONS_SHARP_DEG`, "Make a U-turn onto X"; "to stay
   on X" rather than "onto X" when f's road is named as e's is (a name or a ref, not a class in
   words).
6. Straight on: `continue` "Continue onto X" off a ramp (e a link, f not), so a ramp that
   carries the road's identity still ends; otherwise the same step when e and f share an
   identity (`edge_identity`), when neither has one, or when their roads are named alike (an
   identity change the driver cannot see), and else `continue` "Continue onto X".

An interchange or a roundabout node is a junction like another but for rules 2 and 3: a route
through an interchange without a link onto it (a surface super-junction) turns or goes on by
the rules above.

The first step is `depart`, "Head <compass point> on X", by the first edge's bearing; the last
is `arrive`, "Arrive at <label or 'your destination'>" ("Arrive back where you started" for a
round trip without a label). X is the step's first edge's name as OSM writes it, else its ref
(the first of a `;` list), else its class in words ("a ramp", "an alley", "an unnamed road").

The build keeps no path through a super-junction, so ramps and roundabouts are described by the
edges that enter and leave them: a roundabout's exit has no number, and a ramp is named by the
road it leads to. The compass point is the nearest of eight, half-way bearings going clockwise.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray

from worst_route.config import DIRECTIONS_SHARP_DEG, DIRECTIONS_SLIGHT_DEG, HighwayClass
from worst_route.engine.turns import turn_classes
from worst_route.graph.backend import TURN_LEFT, TURN_STRAIGHT, TURN_U
from worst_route.graph.data import EdgeFlag, GraphData, NodeKind

StepKind = Literal["depart", "continue", "turn", "ramp", "roundabout", "stop", "arrive", "joke"]

COMPASS = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")


@dataclass(frozen=True)
class Step:
    """One instruction: its number, its words, what it drives, and what kind it is."""

    step: int
    text: str
    distance_m: float
    time_s: float
    edge_range: tuple[int, int]  # [a, b) over the route's edge positions
    kind: StepKind

    def as_dict(self) -> dict[str, Any]:
        """The step as JSON."""
        return {
            "step": self.step,
            "text": self.text,
            "distance_m": self.distance_m,
            "time_s": self.time_s,
            "edge_range": list(self.edge_range),
            "kind": self.kind,
        }


@dataclass(frozen=True)
class _Opening:
    """Where a driving step starts: its first edge's position, its kind, its turn word."""

    at: int
    kind: StepKind
    word: str = ""


def directions(
    data: GraphData,
    nodes: ArrayLike,
    edges: ArrayLike,
    stop_idx: Sequence[int],
    *,
    labels: Sequence[str | None],
) -> list[Step]:
    """The route's steps, numbered from 1; `labels` names each stop (None: unnamed)."""
    nodes = np.asarray(nodes, dtype=np.int32)
    edges = np.asarray(edges, dtype=np.int32)
    m = len(edges)
    round_trip = bool(nodes[0] == nodes[-1])
    openings, stops_at = _openings(data, nodes, edges, stop_idx)

    texts = [road_text(data, int(edges[o.at])) for o in openings]
    link = (data.edge_flags[edges] & EdgeFlag.LINK) != 0
    lengths = data.edge_length_m[edges].astype(np.float64).tolist()
    times = data.edge_time_s[edges].astype(np.float64).tolist()
    delays = data.node_delay_s[nodes].astype(np.float64).tolist()

    steps: list[Step] = []
    for k, opening in enumerate(openings):
        a, b = opening.at, openings[k + 1].at if k + 1 < len(openings) else m
        stop = stops_at.get(a)
        if stop is not None:
            name = labels[stop] or f"stop {stop}"
            steps.append(Step(0, f"Arrive at {name}; continue", 0.0, 0.0, (a, a), "stop"))
        # the delays of the nodes the step arrives at, and the start's in the first step
        waits = delays[a + 1 : b + 1] + ([delays[0]] if a == 0 and not round_trip else [])
        steps.append(
            Step(
                step=0,
                text=_text(data, opening, k, openings, texts, link, edges),
                distance_m=math.fsum(lengths[a:b]),
                time_s=math.fsum(times[a:b] + waits),
                edge_range=(a, b),
                kind=opening.kind,
            )
        )
    if labels[-1]:
        arrival = f"Arrive at {labels[-1]}"
    else:
        arrival = "Arrive back where you started" if round_trip else "Arrive at your destination"
    steps.append(Step(0, arrival, 0.0, 0.0, (m, m), "arrive"))
    return numbered(steps)


def numbered(steps: Sequence[Step]) -> list[Step]:
    """The steps numbered from 1 in order."""
    return [
        Step(k, s.text, s.distance_m, s.time_s, s.edge_range, s.kind)
        for k, s in enumerate(steps, start=1)
    ]


def road_text(data: GraphData, edge: int) -> str:
    """How a step names the road of `edge`: its name, else its ref's first part, else its class
    in words."""
    name, ref = int(data.edge_name[edge]), int(data.edge_ref[edge])
    if name >= 0:
        return data.strings.names[name]
    if ref >= 0:
        return data.strings.refs[ref].split(";")[0].strip()
    if data.edge_flags[edge] & EdgeFlag.LINK:
        return "a ramp"
    if data.edge_highway[edge] == HighwayClass.ALLEY:
        return "an alley"
    return "an unnamed road"


def compass(bearing: float) -> str:
    """The eight-point compass direction of a bearing in degrees clockwise from north."""
    return COMPASS[math.floor(bearing / 45 + 0.5) % 8]


def turn_word(turn_class: int, delta: int) -> str:
    """The words for a turn of class `turn_class` whose bearing changes by `delta` degrees."""
    if turn_class == TURN_U:
        return "Make a U-turn"
    side = "left" if turn_class == TURN_LEFT else "right"
    if abs(delta) <= DIRECTIONS_SLIGHT_DEG:
        return f"Slight {side}"
    if abs(delta) > DIRECTIONS_SHARP_DEG:
        return f"Sharp {side}"
    return f"Turn {side}"


def _openings(
    data: GraphData, nodes: NDArray[np.int32], edges: NDArray[np.int32], stop_idx: Sequence[int]
) -> tuple[list[_Opening], dict[int, int]]:
    """Where each driving step starts, and the interior stops by their position."""
    identity = data.edge_identity[edges]
    link = (data.edge_flags[edges] & EdgeFlag.LINK) != 0
    kind = data.node_kind[nodes]
    roads = [road_text(data, int(e)) for e in edges]
    named = (data.edge_name[edges] >= 0) | (data.edge_ref[edges] >= 0)
    classes = turn_classes(data, edges[:-1], edges[1:])
    raw = data.edge_bearing_out[edges[1:]].astype(np.int64) - data.edge_bearing_in[edges[:-1]]
    deltas = (raw + 179) % 360 - 179  # wrapped to (−180, 180]
    stops_at = {int(stop_idx[s]): s for s in range(1, len(stop_idx) - 1)}

    openings = [_Opening(0, "depart")]
    for k in range(1, len(edges)):
        e, f, v = k - 1, k, kind[k]
        turn, delta = int(classes[e]), int(deltas[e])
        alike = roads[e] == roads[f]  # the driver cannot tell the roads apart
        on_ramp = link[e] and link[f]
        off_ramp = link[e] and not link[f]
        if v == NodeKind.ROUNDABOUT:
            openings.append(_Opening(k, "roundabout"))
        elif link[f] and not link[e]:
            openings.append(_Opening(k, "ramp"))
        elif turn != TURN_STRAIGHT and not on_ramp:
            stay = "to stay on" if alike and named[f] else "onto"
            openings.append(_Opening(k, "turn", f"{turn_word(turn, delta)} {stay}"))
        elif off_ramp or (not on_ramp and identity[e] != identity[f] and not alike):
            openings.append(_Opening(k, "continue", "Continue onto"))
        elif k in stops_at:
            openings.append(_Opening(k, "continue", "Continue on"))
    return openings, stops_at


def _text(
    data: GraphData,
    opening: _Opening,
    k: int,
    openings: Sequence[_Opening],
    texts: Sequence[str],
    link: NDArray[np.bool_],
    edges: NDArray[np.int32],
) -> str:
    """The words of the k-th driving step."""
    road = texts[k]
    if opening.kind == "depart":
        return f"Head {compass(float(data.edge_bearing_out[edges[0]]))} on {road}"
    if opening.kind == "roundabout":
        return f"At the roundabout, take the exit onto {road}"
    if opening.kind == "ramp":
        toward = next((texts[j] for j in range(k, len(openings)) if not link[openings[j].at]), None)
        if toward is None:
            first = int(edges[opening.at])
            named = data.edge_name[first] >= 0 or data.edge_ref[first] >= 0
            return f"Take the ramp toward {road}" if named else "Take the ramp"
        return f"Take the ramp toward {toward}"
    return f"{opening.word} {road}"
