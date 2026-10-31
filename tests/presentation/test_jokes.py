"""Jokes: drawn from the route's seed, never twice in a route, spaced, filled in."""

import re
from typing import Any

import numpy as np
import pytest
from numpy.random import PCG64, Generator

from worst_route.config import JOKE_EVERY, JOKE_JITTER
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.presentation import jokes
from worst_route.presentation.directions import Step, numbered
from worst_route.presentation.document import route_directions
from worst_route.presentation.jokes import JOKES, _distance, inject

from ..engine.graphs import at_node, lattice


def drive(n: int, *, turns: bool = True) -> list[Step]:
    """A depart, n − 1 alternating turns and continues, 100 m each, and the arrival."""
    steps = [Step(0, "Head north on A Street", 100.0, 9.0, (0, 1), "depart")]
    for k in range(1, n):
        kind = "turn" if turns and k % 2 else "continue"
        steps.append(Step(0, f"Step {k}", 100.0, 9.0, (k, k + 1), kind))
    steps.append(Step(0, "Arrive at your destination", 0.0, 0.0, (n, n), "arrive"))
    return numbered(steps)


def jokes_of(steps: list[Step]) -> list[Step]:
    return [s for s in steps if s.kind == "joke"]


def test_there_are_about_forty_distinct_jokes() -> None:
    assert 38 <= len(JOKES) <= 45
    assert len(set(JOKES)) == len(JOKES)
    fields = {f for j in JOKES for f in re.findall(r"\{(\w+)\}", j)}
    assert fields == {"ratio", "turns", "turn_count", "distance"}


def test_the_same_seed_tells_the_same_jokes() -> None:
    steps = drive(100)
    one = inject(steps, Generator(PCG64(7)), ratio=12.5)
    two = inject(steps, Generator(PCG64(7)), ratio=12.5)
    other = inject(steps, Generator(PCG64(8)), ratio=12.5)
    assert one == two
    assert [s.text for s in jokes_of(one)] != [s.text for s in jokes_of(other)]


@pytest.mark.parametrize("seed", range(20))
def test_jokes_are_spaced_never_repeated_and_filled_in(seed: int) -> None:
    steps = drive(100)
    out = inject(steps, Generator(PCG64(seed)), ratio=12.5)
    told = jokes_of(out)
    assert len({s.text for s in told}) == len(told) > 0
    assert all("{" not in s.text for s in told)
    # the real steps, in order, renumbered; each joke empty where the step before it ends
    assert [s.text for s in out if s.kind != "joke"] == [s.text for s in steps]
    assert [s.step for s in out] == list(range(1, len(out) + 1))
    for k, s in enumerate(out):
        if s.kind == "joke":
            before = out[k - 1]
            assert before.kind not in ("joke", "arrive", "stop")
            assert s.edge_range == (before.edge_range[1],) * 2
            assert s.distance_m == s.time_s == 0.0
    # the gaps between jokes, counted in driving steps
    driving = 0
    gaps: list[int] = []
    for s in out:
        if s.kind == "joke":
            gaps.append(driving)
            driving = 0
        elif s.edge_range[0] < s.edge_range[1]:
            driving += 1
    low = int(JOKE_EVERY * (1 - JOKE_JITTER))
    high = int(np.ceil(JOKE_EVERY * (1 + JOKE_JITTER)))
    assert all(low <= g <= high for g in gaps)


def test_a_long_route_spreads_its_jokes_over_the_whole_way() -> None:
    steps = drive(2000)
    told = jokes_of(inject(steps, Generator(PCG64(1)), ratio=40.0))
    assert len(JOKES) - 3 <= len(told) <= len(JOKES)  # nearly every one, once
    assert told[-1].edge_range[0] > 1600  # the last near the end, not after step 320


def template_of(text: str) -> str:
    """The joke `text` was filled from."""
    for template in JOKES:
        pattern = re.sub(r"\\\{\w+\\\}", ".+", re.escape(template))
        if re.fullmatch(pattern, text):
            return template
    raise AssertionError(f"no joke gives {text!r}")


def test_placeholders_are_filled_from_the_route() -> None:
    out = inject(drive(400), Generator(PCG64(3)), ratio=42.94)
    filled = {template_of(s.text): (k, s.text) for k, s in enumerate(out) if s.kind == "joke"}
    assert filled["You are {ratio} committed to this."][1] == "You are 42.9× committed to this."
    _, text = filled["You just drove {distance}. On purpose."]
    assert text == "You just drove 330 ft (100 m). On purpose."  # the 100 m step before it
    k, text = filled["Turn number {turns}. Each one was necessary. None of them was necessary."]
    assert text.startswith(f"Turn number {sum(s.kind == 'turn' for s in out[:k])}. ")


def test_one_turn_is_one_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jokes, "JOKES", ("You have now made {turn_count}. A straight line was available.",)
    )
    steps = [Step(0, "Head north on A Street", 100.0, 9.0, (0, 1), "depart")]
    steps.append(Step(0, "Turn left onto B Street", 100.0, 9.0, (1, 2), "turn"))
    steps += [Step(0, f"Step {k}", 100.0, 9.0, (k, k + 1), "continue") for k in range(2, 30)]
    told = jokes_of(inject(numbered(steps), Generator(PCG64(1)), ratio=2.0))
    assert [s.text for s in told] == ["You have now made 1 turn. A straight line was available."]


def test_a_round_trip_has_no_ratio_jokes_and_no_turn_count_before_a_turn() -> None:
    for seed in range(10):
        told = jokes_of(inject(drive(400), Generator(PCG64(seed)), ratio=None))
        assert told and not any("×" in s.text for s in told)
        straight = jokes_of(inject(drive(400, turns=False), Generator(PCG64(seed)), ratio=None))
        assert not any("turn" in s.text.lower() and any(c.isdigit() for c in s.text)
                       for s in straight)  # fmt: skip


def test_a_short_route_has_no_joke() -> None:
    assert jokes_of(inject(drive(3), Generator(PCG64(1)), ratio=2.0)) == []


def test_distances_in_miles_and_kilometres_or_feet_and_metres() -> None:
    assert _distance(100.0) == "330 ft (100 m)"
    assert _distance(3700.0) == "2.3 mi (3.7 km)"
    assert _distance(1609.344) == "1 mi (1.6 km)"
    assert _distance(200_000.0) == "124 mi (200 km)"
    assert _distance(150_000.0) == "93.2 mi (150 km)"
    assert _distance(500_000.0) == "311 mi (500 km)"


def test_route_directions_tells_jokes_unless_asked_not_to() -> None:
    g = lattice(10, 10, rounded_lonlat=True)
    request = RouteRequest(stops=[at_node(g, 0), at_node(g, 99)], seed=2, max_iterations=300)
    route = plan_route(g, request).route

    def told(**fields: Any) -> list[Step]:
        return route_directions(
            g, route.nodes, route.edges, route.stop_idx, labels=[None, None], ratio=3.0, **fields
        )

    plain, funny = told(seed=1, jokes=False), told(seed=1)
    assert jokes_of(plain) == [] and len(jokes_of(funny)) >= 2
    assert [s.text for s in funny if s.kind != "joke"] == [s.text for s in plain]
    assert funny == told(seed=1) and funny != told(seed=2)
