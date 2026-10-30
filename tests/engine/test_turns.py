"""Turn classes by the change of bearing, and a route's turn counts."""

import dataclasses

import numpy as np
import pytest

from worst_route.engine.turns import count_turns, turn_classes
from worst_route.graph.backend import TURN_CLASS, TURN_LEFT, TURN_RIGHT, TURN_STRAIGHT, TURN_U
from worst_route.graph.testing import edge_id

from .graphs import edges_of, lattice


@pytest.mark.parametrize(
    ("bearing_in", "bearing_out", "expected"),
    [
        (0, 0, TURN_STRAIGHT),
        (0, 30, TURN_STRAIGHT),  # the straight band's ends are straight
        (0, 330, TURN_STRAIGHT),
        (0, 31, TURN_RIGHT),
        (0, 90, TURN_RIGHT),
        (0, 160, TURN_RIGHT),  # the turn bands' far ends are turns
        (0, 161, TURN_U),
        (0, 180, TURN_U),  # 180 wraps to +180, a U-turn either way
        (0, 199, TURN_U),
        (0, 200, TURN_LEFT),
        (0, 270, TURN_LEFT),
        (0, 329, TURN_LEFT),
        (350, 10, TURN_STRAIGHT),  # across north: +20
        (10, 350, TURN_STRAIGHT),
        (300, 30, TURN_RIGHT),  # +90 across north
        (30, 300, TURN_LEFT),  # −90 across north
        (90, 0, TURN_LEFT),  # east, then north
        (90, 180, TURN_RIGHT),  # east, then south
        (359, 179, TURN_U),
    ],
)
def test_the_classes_by_the_change_of_bearing(
    bearing_in: int, bearing_out: int, expected: int
) -> None:
    assert TURN_CLASS[(bearing_out - bearing_in) % 360] == expected


def test_the_table_against_the_rule_written_out() -> None:
    for d in range(360):
        delta = d - 360 if d > 180 else d
        if abs(delta) <= 30:
            expected = TURN_STRAIGHT
        elif 30 < delta <= 160:
            expected = TURN_RIGHT
        elif -160 <= delta < -30:
            expected = TURN_LEFT
        else:
            expected = TURN_U
        assert TURN_CLASS[d] == expected, d
    assert not TURN_CLASS.flags.writeable


def test_turns_on_a_lattice() -> None:
    # node r * 4 + c, row 0 the southmost: east along row 0, north up column 3, west along
    # row 3, south, then east and round to the right
    g = lattice(4, 4)
    route = [0, 1, 2, 3, 7, 11, 15, 14, 13, 9, 10, 6, 5]
    edges = edges_of(g, route)
    classes = turn_classes(g, edges[:-1], edges[1:]).tolist()
    s, left, right = TURN_STRAIGHT, TURN_LEFT, TURN_RIGHT
    assert classes == [s, s, left, s, s, left, s, left, left, right, right]
    assert count_turns(g, edges) == {"left": 4, "right": 2, "straight": 5, "u_turn": 0}
    assert count_turns(g, edges[:1]) == {"left": 0, "right": 0, "straight": 0, "u_turn": 0}


def test_a_u_turn_and_the_stored_bearings_decide() -> None:
    g = lattice(2, 3)
    out, back = edge_id(g, 0, 1), edge_id(g, 1, 0)
    assert turn_classes(g, np.array([out]), np.array([back])).tolist() == [TURN_U]
    # the classes read the stored bearings, not the nodes' positions: a junction whose edges
    # arrive and leave bent (a super-junction's boundary edges) is classed by them
    up = edge_id(g, 1, 4)
    bent = g.edge_bearing_out.copy()
    bent[up] = 120
    g = dataclasses.replace(g, edge_bearing_out=bent)
    assert turn_classes(g, np.array([out]), np.array([up])).tolist() == [TURN_STRAIGHT]
