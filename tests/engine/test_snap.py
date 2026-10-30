import numpy as np
import pytest

from worst_route.engine.snap import (
    StopUnreachable,
    apart_from,
    is_round_trip,
    resnap,
    role_of,
    snap_stops,
)
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import make_graph

from .graphs import X0, Y0, at_node


def town():
    """A two-way 3 × 3 lattice (node r * 3 + c) with a cul-de-sac 5 ↔ 9 east of node 5."""
    xy = [(X0 + 100.0 * c, Y0 + 100.0 * r) for r in range(3) for c in range(3)]
    xy.append((X0 + 300.0, Y0 + 100.0))
    streets = [(a, a + 1) for a in (0, 1, 3, 4, 6, 7)] + [(a, a + 3) for a in range(6)] + [(5, 9)]
    edges = [(a, b, 100.0) for a, b in streets] + [(b, a, 100.0) for a, b in streets]
    return make_graph(xy, edges)


TOWN = town()
INDEX = SpatialIndex(TOWN)
EVERYWHERE = np.ones(TOWN.n, dtype=bool)


def test_roles() -> None:
    assert [role_of(k, 4) for k in range(4)] == ["start", "via", "via", "end"]
    assert [role_of(k, 2) for k in range(2)] == ["start", "end"]


def test_a_cul_de_sac_via_moves_to_the_through_street() -> None:
    snaps = snap_stops(
        INDEX, TOWN, [at_node(TOWN, 0), at_node(TOWN, 9), at_node(TOWN, 8)], EVERYWHERE
    )
    assert [s.node for s in snaps] == [0, 5, 8]
    assert snaps[1].note == "moved 100 m to the nearest through street"
    assert snaps[0].note is None and snaps[2].note is None
    ends = snap_stops(INDEX, TOWN, [at_node(TOWN, 9), at_node(TOWN, 0)], EVERYWHERE)
    assert [s.node for s in ends] == [9, 0]  # a cul-de-sac can start or end a trip


def test_a_via_on_another_stop_moves() -> None:
    snaps = snap_stops(
        INDEX, TOWN, [at_node(TOWN, 4), at_node(TOWN, 4), at_node(TOWN, 8)], EVERYWHERE
    )
    assert [s.node for s in snaps] == [4, 1, 8]
    assert snaps[1].note == "moved 100 m: another stop is at the nearest node"


def test_an_end_on_the_start_is_a_round_trip_but_not_on_a_via() -> None:
    snaps = snap_stops(INDEX, TOWN, [at_node(TOWN, 4), at_node(TOWN, 4)], EVERYWHERE)
    assert [s.node for s in snaps] == [4, 4] and snaps[1].note is None
    snaps = snap_stops(
        INDEX, TOWN, [at_node(TOWN, 0), at_node(TOWN, 4), at_node(TOWN, 4)], EVERYWHERE
    )
    assert [s.node for s in snaps] == [0, 4, 1]
    assert apart_from(snaps, 2, 3, round_trip=False) == [0, 4]
    assert apart_from(snaps, 2, 3, round_trip=True) == [4]
    assert apart_from(snaps, 1, 3, round_trip=True) == [0, 1]


def test_only_identical_points_make_a_round_trip() -> None:
    lat, lon = at_node(TOWN, 4)
    assert is_round_trip([(lat, lon), (lat, lon)])
    assert is_round_trip([(lat, lon), at_node(TOWN, 8), (lat, lon)])
    assert not is_round_trip([(lat, lon), (lat + 1e-7, lon)])


def test_an_end_whose_nearest_node_is_the_start_moves_on() -> None:
    # a click a few metres from the start's node is not a round trip: the end takes the next node
    lat, lon = at_node(TOWN, 4)
    snaps = snap_stops(INDEX, TOWN, [(lat, lon), (lat + 0.00003, lon)], EVERYWHERE)
    assert snaps[0].node == 4 and snaps[1].node != 4
    assert snaps[1].note is not None
    assert snaps[1].note.endswith("m: another stop is at the nearest node")


def test_resnap_takes_the_next_candidate_and_says_why() -> None:
    exclude = np.zeros(TOWN.n, dtype=bool)
    exclude[[4, 1, 5, 7]] = True
    snap = resnap(INDEX, at_node(TOWN, 4), "via", EVERYWHERE, exclude)
    assert snap.node == 3
    assert snap.note == "moved 100 m: nearer nodes cannot be reached without repeating one"


def test_stop_unreachable_names_the_leg() -> None:
    error = StopUnreachable((1, 2), "after moving stop 1 and stop 2 up to 10 times each")
    assert error.leg == (1, 2) and error.stop_index == 2
    assert str(error) == (
        "no route from stop 1 to stop 2 without repeating a road, "
        "after moving stop 1 and stop 2 up to 10 times each"
    )
    with pytest.raises(StopUnreachable):
        raise error
