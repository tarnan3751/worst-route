"""Turns: the class of each turn, and a route's counts (`docs/PLAN.md` §3.6;
`docs/reference/engine.md`, **Turns**).

A turn from edge e into edge f (f leaving the node e arrives at) is classed by its change of
bearing, `delta = wrap(bearing_out(f) − bearing_in(e))` in (−180, 180] degrees clockwise:
straight when |delta| ≤ `TURN_STRAIGHT_DEG` (30), right when `TURN_STRAIGHT_DEG` < delta ≤
`TURN_UTURN_DEG` (160), left when −`TURN_UTURN_DEG` ≤ delta < −`TURN_STRAIGHT_DEG`, and a U-turn
beyond. The bearings are the build's (`edge_bearing_in`, over the last `BEARING_SAMPLE_M` of e,
and `edge_bearing_out`, over the first of f). At a super-junction (an interchange or a
roundabout, consolidated to one node) a turn is therefore classed by the bearings of the edge
that enters it and the edge that leaves it, whatever the way through it looks like: coarse,
but the junction's inside is not part of the route.

The rule is `graph.backend.TURN_CLASS`, which the backends' searches also read when a
constraint bans some classes; a class's code is its bit in such a set (`TURN_LEFT` 1,
`TURN_RIGHT` 2, `TURN_U` 4, and `TURN_STRAIGHT` 0, never banned).

`turn_classes(data, e_in, e_out)` classes each turn (e_in[k], e_out[k]); `count_turns(data,
edges)` counts the turns between a route's consecutive edges by class.
"""

import numpy as np
from numpy.typing import NDArray

from worst_route.graph.backend import TURN_CLASS, TURN_LEFT, TURN_RIGHT, TURN_STRAIGHT, TURN_U
from worst_route.graph.data import GraphData

TURN_NAMES = {TURN_LEFT: "left", TURN_RIGHT: "right", TURN_STRAIGHT: "straight", TURN_U: "u_turn"}


def turn_classes(
    data: GraphData, e_in: NDArray[np.int32], e_out: NDArray[np.int32]
) -> NDArray[np.uint8]:
    """The class of each turn from e_in[k] into e_out[k]."""
    delta = data.edge_bearing_out[e_out].astype(np.int64) - data.edge_bearing_in[e_in]
    classes: NDArray[np.uint8] = TURN_CLASS[delta % 360]
    return classes


def count_turns(data: GraphData, edges: NDArray[np.int32]) -> dict[str, int]:
    """How many of the route's turns are left, right, straight and U-turns."""
    counts = np.bincount(turn_classes(data, edges[:-1], edges[1:]), minlength=TURN_U + 1)
    return {name: int(counts[code]) for code, name in TURN_NAMES.items()}
