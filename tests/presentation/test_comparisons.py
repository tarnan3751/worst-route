"""`comparison_line`: the rule (the longest thing strictly exceeded; "N×" from twice its length;
"shorter than" below the shortest), every thing reached, the distance format, determinism."""

import numpy as np
import pytest

from worst_route.presentation.comparisons import (
    COMPARISONS,
    METRES_PER_MILE,
    SHORTER,
    Comparison,
    comparison_line,
)


def metres(miles: float) -> float:
    return miles * METRES_PER_MILE


@pytest.mark.parametrize(
    ("km", "line"),
    [
        (5, "Longer than a lap of the Indianapolis Motor Speedway (2.5 mi, 4 km)."),
        (90, "2.1× the Boston Marathon (26.2 mi, 42.2 km)."),
        (480, "Longer than driving from Portland to Seattle (174 mi, 280 km)."),
        (5000, "Longer than I-90 from Seattle to Boston (3,020 mi, 4,860 km)."),
    ],
)
def test_the_lines_for_some_lengths(km: float, line: str) -> None:
    assert comparison_line(km * 1000) == line


def test_the_things_run_shortest_first_from_about_a_mile_to_the_moon() -> None:
    miles = [c.miles for c in COMPARISONS]
    assert miles == sorted(miles) and len(set(miles)) == len(miles) == 15
    assert miles[0] < 2 and miles[-1] == 238_900
    for c in COMPARISONS:
        assert c.longer.startswith("Longer than ") and "{dist}" in c.longer
        assert c.times.startswith("{n}× ") and "{dist}" in c.times


def test_a_sweep_reaches_every_thing_and_gives_each_length_one_line() -> None:
    lengths = metres(np.geomspace(0.1, 500_000, 4000))
    lines = [comparison_line(float(m)) for m in lengths]
    assert lines == [comparison_line(float(m)) for m in lengths]  # no state
    seen = set(lines)
    assert any(line.startswith("Shorter than") for line in seen)
    for c in COMPARISONS:
        assert any(line.startswith(c.longer.split("{")[0]) for line in seen), c.longer
    # "N×" needs a route twice a thing's length, which is past the next thing when that one is
    # less than twice as long: from the data, which things never get their "N×" line.
    following = [*COMPARISONS[1:], None]
    never = {
        c.miles
        for c, after in zip(COMPARISONS, following, strict=True)
        if after is not None and after.miles <= 2 * c.miles
    }
    times_seen = {c.miles for c in COMPARISONS if any(_times(c) in line for line in seen)}
    assert {c.miles for c in COMPARISONS} - times_seen == never
    assert len(never) == 7  # docs/reference/presentation.md, **Comparisons**, names them


def _times(c: Comparison) -> str:
    """The part of a thing's "N×" line after the number and before its distance."""
    return c.times.split("{n}")[1].split("{")[0]


def test_the_boundaries() -> None:
    gate, bridge = COMPARISONS[0], COMPARISONS[1]
    shorter = SHORTER.format(dist="1.7 mi, 2.7 km")
    assert comparison_line(0.0) == comparison_line(metres(1.7)) == shorter  # equal is not longer
    assert comparison_line(metres(1.7) + 1) == gate.longer.format(dist="1.7 mi, 2.7 km")
    assert comparison_line(metres(2.5)).startswith("Longer than the Golden Gate Bridge")
    assert comparison_line(metres(2.6)) == bridge.longer.format(dist="2.5 mi, 4 km")
    assert comparison_line(metres(4.99)).startswith("Longer than a lap")  # 1.996×
    assert comparison_line(metres(5.0)).startswith("2.0× a lap")
    assert comparison_line(metres(13.39)).startswith("5.4× a lap")
    assert comparison_line(metres(500_000)) == (
        "2.1× the distance to the Moon (238,900 mi, 384,472 km)."
    )


def test_the_distances_read_as_miles_with_kilometres() -> None:
    dists = [comparison_line(metres(c.miles) + 1).split(" (")[1] for c in COMPARISONS]
    assert dists == [
        "1.7 mi, 2.7 km).",
        "2.5 mi, 4 km).",
        "13.4 mi, 21.6 km).",
        "26.2 mi, 42.2 km).",
        "72 mi, 116 km of shoreline).",
        "174 mi, 280 km).",
        "363 mi, 584 km).",
        "1,381 mi, 2,223 km).",
        "2,448 mi, 3,940 km).",
        "2,790 mi, 4,490 km).",
        "3,020 mi, 4,860 km).",
        "5,500 mi, 8,851 km).",
        "19,000 mi, 30,578 km).",
        "24,901 mi, 40,074 km).",
        "238,900 mi, 384,472 km).",
    ]
