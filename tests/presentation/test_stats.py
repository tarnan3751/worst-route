"""Stats and toasts: fuel and cost, states and towns, the share of the roads, the toasts."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from worst_route.api.schemas import Vehicle
from worst_route.config import TOASTS_MAX
from worst_route.graph.geometry import straight
from worst_route.presentation.places import PlaceIndex
from worst_route.presentation.stats import Stats, stats, toasts

from .support import EAST_THEN_NORTH, EAST_THEN_NORTH_EDGES, GRID, write_fixture_places

ALL = np.ones(len(GRID.node_lon), dtype=np.bool_)
NONE = np.zeros(len(GRID.edge_u), dtype=np.bool_)
NODES = np.array(EAST_THEN_NORTH, dtype=np.int32)
LOOP = np.array([0, 1, 11, 10, 0], dtype=np.int32)


def stats_of(**fields: Any) -> Stats:
    values: dict[str, Any] = {
        "length_m": 10_000.0, "baseline_length_m": 3_000.0, "vehicle": None, "places": None,
        "geometry": straight(GRID.m), "active": ALL, "banned": NONE, **fields,
    }  # fmt: skip
    return stats(GRID, EAST_THEN_NORTH_EDGES, **values)


def test_fuel_and_cost_come_from_the_vehicle() -> None:
    s = stats_of(vehicle=Vehicle(mpg=24, gas_price_usd=3.89))
    miles = 7000 / 1609.344
    assert s.extra_fuel_gal == pytest.approx(miles / 24)
    assert s.extra_cost_usd == pytest.approx(miles / 24 * 3.89)
    plain = stats_of()
    assert plain.extra_fuel_gal is None and plain.extra_cost_usd is None


def test_states_and_towns_come_from_the_places_index(data_dir: Path) -> None:
    write_fixture_places(data_dir)
    index = PlaceIndex.load("portland", data_dir)
    assert index is not None
    s = stats_of(places=index)
    assert s.states == ["Westland", "Eastland"]
    assert s.towns == ["Westville", "Middlefield", "Eastbrook"] and s.towns_n == 3
    plain = stats_of()
    assert plain.states is None and plain.towns is None and plain.towns_n is None
    assert set(s.metrics()) == {
        "extra_fuel_gal", "extra_cost_usd", "states", "towns", "towns_n", "network_fraction",
    }  # fmt: skip


def test_the_network_fraction_counts_road_units_within_reach() -> None:
    # 17 one-way edges of the lattice's 360, each its own road unit
    assert stats_of().network_fraction == pytest.approx(17 / 360)
    # edges the request bans, or with an end outside its area, are out of reach
    banned = NONE.copy()
    banned[:40] = True
    assert stats_of(banned=banned).network_fraction == pytest.approx(17 / 320)
    active = ALL.copy()
    active[0] = False  # node 0's 4 edges (2 out, 2 in)
    assert stats_of(active=active).network_fraction == pytest.approx(17 / 356)
    # two edges of one unit count once
    units = GRID.edge_unit.copy()
    units[1] = units[0]
    paired = replace(GRID, edge_unit=units)
    reach = stats(
        paired, EAST_THEN_NORTH_EDGES, length_m=1.0, baseline_length_m=1.0, vehicle=None,
        places=None, geometry=None, active=ALL, banned=NONE,
    )  # fmt: skip
    assert reach.network_fraction == pytest.approx(17 / 359)


def metrics(**fields: Any) -> dict[str, Any]:
    return {
        "ratio": 42.94, "time_s": 28_620.0, "delay_s": 10_620.0, "baseline_time_s": 784.0,
        "baseline_delay_s": 520.0, "turns": {"left": 717, "right": 733, "straight": 1356,
        "u_turn": 12}, **fields,
    }  # fmt: skip


FULL = Stats(3.42, 13.2, ["Oregon", "Washington"], ["Portland", "Gresham"], 2, 0.1234)
BARE = Stats(None, None, None, None, None, 0.1234)


def test_the_toasts_come_most_striking_first() -> None:
    lines = toasts(FULL, metrics(), nodes=NODES, edges=EAST_THEN_NORTH_EDGES)
    assert lines == [
        "42.9× longer than it had to be.",
        "Passes through 2 towns. Portland twice.",
        "An extra 3.4 gallons, $13.20, for nothing.",
        "10 h 54 min on the road instead of 21 min.",
        "2 h 57 min waiting at signals and stop signs.",
        "18 intersections, none of them twice.",
        "17 stretches of road, each driven exactly once.",
    ]
    assert toasts(FULL, metrics(), nodes=NODES, edges=EAST_THEN_NORTH_EDGES) == lines


def test_without_places_or_a_vehicle_there_are_still_four() -> None:
    no_delays = metrics(delay_s=0.0, baseline_delay_s=0.0)
    lines = toasts(BARE, no_delays, nodes=NODES, edges=EAST_THEN_NORTH_EDGES)
    assert len(lines) == 4
    assert lines[0] == "42.9× longer than it had to be."
    assert not any("gallons" in line or "towns" in line or "waiting" in line for line in lines)


def test_toasts_that_depend_on_the_route() -> None:
    def lines(s: Stats = BARE, **fields: Any) -> list[str]:
        return toasts(s, metrics(**fields), nodes=NODES, edges=EAST_THEN_NORTH_EDGES)

    no_left = lines(turns={"left": 0, "right": 1, "straight": 5, "u_turn": 0})
    assert no_left[1] == "Never turns left. 1 right turn."
    assert not any(" left turns and " in line for line in no_left)
    assert lines(turns={"left": 3, "right": 0, "straight": 5, "u_turn": 0})[1] == (
        "Never turns right. 3 left turns."
    )
    assert "Never leaves Portland. Sees all of it instead." in lines(
        replace(BARE, towns=["Portland"], towns_n=1)
    )
    # what the stat line already says: the states, the turns both ways, the share of the roads
    said = lines(replace(BARE, states=["Oregon", "Washington"], network_fraction=0.001))
    assert not any("Oregon" in line or " % " in line or " left turns" in line for line in said)
    collecting = lines(items={"toll_booth": 0, "level_crossing": 1, "roundabout": 2, "bridge": 37})
    assert collecting[-1] == "Passes 1 level crossing, 2 roundabouts and 37 bridges on the way."
    assert not any("Passes " in line for line in lines(items={"bridge": 0, "roundabout": 0}))


def test_a_round_trip_goes_nowhere() -> None:
    loop = metrics(ratio=None, baseline_time_s=0.0, baseline_delay_s=0.0)
    lines = toasts(BARE, loop, nodes=LOOP, edges=LOOP[:-1])
    assert lines[0] == "Ends exactly where it began."
    assert "10 h 54 min on the road to get nowhere." in lines
    assert "4 intersections, none of them twice." in lines  # the start once
    assert not any("×" in line for line in lines)


def test_a_round_trip_through_a_via_has_a_ratio_first() -> None:
    lines = toasts(BARE, metrics(), nodes=LOOP, edges=LOOP[:-1])
    assert lines[:2] == [
        "42.9× longer than it had to be.",
        "Ends exactly where it began.",
    ]
    assert "10 h 54 min on the road instead of 21 min." in lines


def test_the_items_toast_is_never_dropped() -> None:
    items = {"toll_booth": 0, "level_crossing": 0, "roundabout": 0, "bridge": 3}
    lines = toasts(FULL, metrics(items=items), nodes=NODES, edges=EAST_THEN_NORTH_EDGES)
    assert len(lines) == 8 and lines[-1] == "Passes 3 bridges on the way."
    # every toast a route can have at once: a round trip through a via, never turning left
    no_left = {"left": 0, "right": 5, "straight": 9, "u_turn": 0}
    lines = toasts(FULL, metrics(items=items, turns=no_left), nodes=LOOP, edges=LOOP[:-1])
    assert len(lines) == TOASTS_MAX == 10 and lines[-1] == "Passes 3 bridges on the way."
