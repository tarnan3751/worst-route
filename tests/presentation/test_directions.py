"""Directions: each kind of step with its words, the ranges, the distances and the times."""

import math
from pathlib import Path

import numpy as np
import pytest

from worst_route.config import HighwayClass
from worst_route.engine.objectives.base import route_delay_s
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.graph.data import GraphData, NodeKind
from worst_route.graph.testing import Edge, make_graph
from worst_route.pipeline.save import graph_data
from worst_route.presentation.directions import Step, compass, directions, road_text

from ..engine.graphs import at_node, edges_of, lattice
from ..pipeline.chain import FIXTURES, enriched, tier_nodes
from ..pipeline.test_save import META
from .streets import ROWS, X0, Y0, corner, highway, star, town, us26, with_kind


def steps_of(
    g: GraphData,
    nodes: list[int],
    stop_idx: tuple[int, ...] | None = None,
    labels: list[str | None] | None = None,
) -> list[Step]:
    stops = stop_idx if stop_idx is not None else (0, len(nodes) - 1)
    return directions(
        g,
        np.array(nodes, dtype=np.int32),
        edges_of(g, nodes),
        stops,
        labels=labels if labels is not None else [None] * len(stops),
    )


def texts(steps: list[Step]) -> list[str]:
    return [s.text for s in steps]


def check_tiling(steps: list[Step], m: int) -> None:
    """The driving steps' ranges tile [0, m) in order; the others are empty, in place."""
    at = 0
    for s in steps:
        a, b = s.edge_range
        if s.kind in ("stop", "arrive", "joke"):
            assert a == b == at
        else:
            assert a == at < b
            at = b
    assert at == m
    assert [s.step for s in steps] == list(range(1, len(steps) + 1))


def test_a_town_route_departs_turns_and_arrives() -> None:
    g = town()
    # east along Main Street for two blocks, left up 3rd Avenue, right along Oak, left up 4th
    steps = steps_of(g, [0, 1, 2, 6, 7, 11])
    assert texts(steps) == [
        "Head east on Southeast Main Street",
        "Turn left onto Southeast 3rd Avenue",
        "Turn right onto Southeast Oak Street",
        "Turn left onto Southeast 4th Avenue",
        "Arrive at your destination",
    ]
    assert [s.kind for s in steps] == ["depart", "turn", "turn", "turn", "arrive"]
    assert [s.edge_range for s in steps] == [(0, 2), (2, 3), (3, 4), (4, 5), (5, 5)]
    check_tiling(steps, 5)
    assert [s.distance_m for s in steps] == pytest.approx([200, 100, 100, 100, 0])
    assert steps[0].time_s == pytest.approx(200 / (40 / 3.6))


def test_an_interior_stop_is_its_own_step_and_the_road_goes_on() -> None:
    g = town()
    steps = steps_of(g, [0, 1, 2, 3, 7], (0, 1, 4), ["Home", "Powell's", "Reed"])
    assert texts(steps) == [
        "Head east on Southeast Main Street",
        "Arrive at Powell's; continue",
        "Continue on Southeast Main Street",
        "Turn left onto Southeast 4th Avenue",
        "Arrive at Reed",
    ]
    assert [s.kind for s in steps] == ["depart", "stop", "continue", "turn", "arrive"]
    assert steps[1].edge_range == (1, 1) and steps[1].distance_m == steps[1].time_s == 0
    check_tiling(steps, 4)

    # unnamed stops, the via turning off the road it came along
    steps = steps_of(g, [0, 1, 5, 6], (0, 2, 3))
    assert texts(steps) == [
        "Head east on Southeast Main Street",
        "Turn left onto Southeast 2nd Avenue",
        "Arrive at stop 1; continue",
        "Turn right onto Southeast Oak Street",
        "Arrive at your destination",
    ]


def test_a_round_trip_arrives_back_where_it_started() -> None:
    g = town()
    loop = [0, 1, 5, 4, 0]
    assert steps_of(g, loop)[-1].text == "Arrive back where you started"
    assert steps_of(g, loop, labels=["Home", "Home"])[-1].text == "Arrive at Home"


@pytest.mark.parametrize(
    ("angle", "words"),
    [
        (31, "Slight right"),
        (45, "Slight right"),
        (46, "Turn right"),
        (120, "Turn right"),
        (121, "Sharp right"),
        (160, "Sharp right"),
        (161, "Make a U-turn"),
        (-31, "Slight left"),
        (-45, "Slight left"),
        (-46, "Turn left"),
        (-120, "Turn left"),
        (-121, "Sharp left"),
        (-160, "Sharp left"),
        (-161, "Make a U-turn"),
    ],
)
def test_the_turn_words_follow_the_change_of_bearing(angle: int, words: str) -> None:
    g = star([angle])
    steps = steps_of(g, [1, 0, 2])
    assert texts(steps) == [
        "Head north on Approach Road",
        f"{words} onto Spoke {angle}",
        "Arrive at your destination",
    ]
    assert steps[1].kind == "turn"


def test_straight_on_continues_only_when_the_road_changes() -> None:
    g = star([0, 30], approach="Spoke 0")
    # the same name straight on is one step; a new one within 30 degrees is "Continue onto"
    assert texts(steps_of(g, [1, 0, 2]))[:-1] == ["Head north on Spoke 0"]
    steps = steps_of(g, [1, 0, 3])
    assert texts(steps)[:-1] == ["Head north on Spoke 0", "Continue onto Spoke 30"]
    assert steps[1].kind == "continue"


def test_a_turn_is_a_step_even_on_the_same_road() -> None:
    g = corner()
    steps = steps_of(g, [0, 1, 2])
    assert texts(steps) == [
        "Head east on Bend Road",
        "Turn left to stay on Bend Road",
        "Arrive at your destination",
    ]
    assert steps[1].kind == "turn"
    # unnamed roads in words, merged straight on, cut at a turn, never "to stay on" one
    assert texts(steps_of(g, [0, 1, 3, 4, 5])) == [
        "Head east on Bend Road",
        "Continue onto Other Street",
        "Continue onto an unnamed road",
        "Turn left onto an alley",
        "Arrive at your destination",
    ]
    assert texts(steps_of(g, [3, 4, 6]))[1] == "Turn right onto an unnamed road"


def test_a_road_that_changes_its_identity_but_not_its_name_goes_on() -> None:
    # Sheridan Street gains the ref US 26 straight on: no "Continue onto" the same name; then
    # US 26 turns twice with the road's name and once on the same name: each turn a step
    g = us26()
    steps = steps_of(g, [0, 1, 2, 3, 4, 5])
    assert texts(steps) == [
        "Head east on Southwest Sheridan Street",
        "Turn right onto Southwest 3rd Avenue",
        "Turn left onto Southwest Arthur Street",
        "Turn left to stay on Southwest Arthur Street",
        "Arrive at your destination",
    ]
    assert [s.edge_range for s in steps] == [(0, 2), (2, 3), (3, 4), (4, 5), (5, 5)]


def test_a_ramp_names_the_road_it_leads_to() -> None:
    g = highway()
    steps = steps_of(g, [0, 1, 2, 3, 4, 5])
    assert texts(steps) == [
        "Head east on Southeast Main Street",
        "Take the ramp toward I 5",
        "Continue onto I 5",
        "Arrive at your destination",
    ]
    assert [s.kind for s in steps] == ["depart", "ramp", "continue", "arrive"]
    assert [s.edge_range for s in steps] == [(0, 1), (1, 3), (3, 5), (5, 5)]
    # a route that ends on the ramp
    assert texts(steps_of(g, [0, 1, 2])) == [
        "Head east on Southeast Main Street",
        "Take the ramp",
        "Arrive at your destination",
    ]
    assert road_text(g, int(edges_of(g, [1, 2])[0])) == "a ramp"
    # an interchange on the ramp, or where it meets the motorway, adds no second ramp
    for hub in (2, 3):
        steps = steps_of(with_kind(g, hub, NodeKind.INTERCHANGE), [0, 1, 2, 3, 4, 5])
        assert texts(steps)[1:3] == ["Take the ramp toward I 5", "Continue onto I 5"]


def test_a_ramp_that_carries_the_road_s_ref_still_ends() -> None:
    # US 30 on Portland: its ramp carries the ref of the road it joins straight on, which must
    # not swallow the road; the ramp leads toward it, and a step goes on along it
    nodes = [(X0 + x, Y0 + y) for x, y in ((0, 0), (100, 0), (200, 50), (300, 100), (400, 100))]
    edges = [
        Edge(0, 1, name="Main Street"),
        Edge(1, 2, highway=HighwayClass.PRIMARY_LINK, ref="US 30"),
        Edge(2, 3, highway=HighwayClass.PRIMARY, name="Northwest Saint Helens Road", ref="US 30"),
        Edge(3, 4, highway=HighwayClass.PRIMARY, name="Northwest Yeon Avenue"),
    ]
    g = make_graph(nodes, edges)
    steps = steps_of(g, [0, 1, 2, 3, 4])
    assert texts(steps) == [
        "Head east on Main Street",
        "Take the ramp toward Northwest Saint Helens Road",
        "Continue onto Northwest Saint Helens Road",
        "Continue onto Northwest Yeon Avenue",
        "Arrive at your destination",
    ]
    assert [s.edge_range for s in steps] == [(0, 1), (1, 2), (2, 3), (3, 4), (4, 4)]


def test_super_junctions_by_their_kind() -> None:
    g = star([-90, 0], approach="Spoke 0")
    # an interchange without a link onto it is a junction like another
    interchange = with_kind(g, 0, NodeKind.INTERCHANGE)
    assert texts(steps_of(interchange, [1, 0, 2]))[1] == "Turn left onto Spoke -90"
    assert len(steps_of(interchange, [1, 0, 3])) == 2  # the same road through it: one step
    roundabout = with_kind(g, 0, NodeKind.ROUNDABOUT)
    for spoke, road in ((2, "Spoke -90"), (3, "Spoke 0")):  # the same road too
        steps = steps_of(roundabout, [1, 0, spoke])
        assert steps[1].text == f"At the roundabout, take the exit onto {road}"
        assert steps[1].kind == "roundabout" and steps[1].edge_range == (1, 2)


def fixture_build(name: str, tmp_path: Path) -> GraphData:
    graph = enriched(FIXTURES / f"{name}.osm", tmp_path)
    return graph_data(graph, tier_nodes(graph), META)


def test_the_diamond_interchange_from_the_pipeline(tmp_path: Path) -> None:
    g = fixture_build("diamond", tmp_path)
    (hub,) = np.flatnonzero(g.node_kind == NodeKind.INTERCHANGE).tolist()
    south = int(g.edge_u[(g.edge_v == hub) & (g.edge_bearing_in == 0)][0])  # I 5 northbound
    west = int(g.edge_v[(g.edge_u == hub) & (g.edge_bearing_out == 270)][0])  # SE Main St
    north = int(g.edge_v[(g.edge_u == hub) & (g.edge_bearing_out == 0)][0])  # I 5 on north
    # the ramps are inside the super-junction: from the edges around it, a left turn
    assert texts(steps_of(g, [south, hub, west])) == [
        "Head north on I 5",
        "Turn left onto SE Main St",
        "Arrive at your destination",
    ]
    assert texts(steps_of(g, [south, hub, north])) == [
        "Head north on I 5",
        "Arrive at your destination",
    ]


def test_the_roundabout_from_the_pipeline(tmp_path: Path) -> None:
    g = fixture_build("roundabout", tmp_path)
    (hub,) = np.flatnonzero(g.node_kind == NodeKind.ROUNDABOUT).tolist()
    north = int(g.edge_u[(g.edge_v == hub) & (g.edge_bearing_in == 180)][0])  # NE 42nd Ave
    east = int(g.edge_v[(g.edge_u == hub) & (g.edge_bearing_out == 90)][0])  # NE Going St
    south = int(g.edge_v[(g.edge_u == hub) & (g.edge_bearing_out == 180)][0])  # NE 42nd Ave
    assert texts(steps_of(g, [north, hub, east])) == [
        "Head south on NE 42nd Ave",
        "At the roundabout, take the exit onto NE Going St",
        "Arrive at your destination",
    ]
    assert texts(steps_of(g, [north, hub, south]))[1] == (
        "At the roundabout, take the exit onto NE 42nd Ave"
    )


def test_a_planned_route_s_steps_add_up_to_its_length_and_time() -> None:
    g = lattice(10, 10, rounded_lonlat=True, primary_every=3)  # signals and stop signs
    for stops in ((0, 45, 99), (27, 27)):  # three stops; a round trip
        request = RouteRequest(
            stops=[at_node(g, v) for v in stops], seed=4, max_iterations=150, budget_s=120
        )
        result = plan_route(g, request)
        route = result.route
        steps = directions(g, route.nodes, route.edges, route.stop_idx, labels=[None] * len(stops))
        check_tiling(steps, len(route.edges))
        assert math.fsum(s.distance_m for s in steps) == pytest.approx(route.metrics.length_m)
        experienced = route.metrics.time_s + route_delay_s(g, route.nodes)
        assert route_delay_s(g, route.nodes) > 0
        assert math.fsum(s.time_s for s in steps) == pytest.approx(experienced)
        assert all(s.text for s in steps)
        assert sum(s.kind == "stop" for s in steps) == len(stops) - 2


def test_names_refs_and_the_compass() -> None:
    g = highway()
    assert road_text(g, int(edges_of(g, [3, 4])[0])) == "I 5"  # the first of "I 5;US 30"
    assert road_text(g, int(edges_of(g, [0, 1])[0])) == "Southeast Main Street"
    points = [0, 22, 23, 67, 68, 90, 135, 180, 225, 270, 315, 337, 338, 359]
    assert [compass(b) for b in points] == [
        "north", "north", "northeast", "northeast", "east", "east", "southeast", "south",
        "southwest", "west", "northwest", "northwest", "north", "north",
    ]  # fmt: skip
    # half-way bearings all go clockwise
    halves = [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]
    assert [compass(b) for b in halves] == [
        "northeast",
        "east",
        "southeast",
        "south",
        "southwest",
        "west",
        "northwest",
        "north",
    ]
    assert ROWS[0] == "Southeast Main Street"
