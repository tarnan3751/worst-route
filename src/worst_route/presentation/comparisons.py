"""The comparison line: how a route's length compares with something people know
(`docs/reference/presentation.md`, **Comparisons**).

`COMPARISONS` lists the things, shortest first, each with its length in miles and two deadpan
lines: `longer` ("Longer than ..., which is {dist}.") and `times` ("{n}× ..., which is
{dist}."). `comparison_line(length_m)` picks the longest thing the route is strictly longer
than: its `times` line when the route is at least twice as long (`{n}` the multiple, one
decimal), else its `longer` line; a route shorter than every thing gets `SHORTER`. `{dist}` is the
thing's length in miles with kilometres in parentheses (`distance_text`). No randomness and no
state: the same length always gives the same line.
"""

from dataclasses import dataclass

METRES_PER_MILE = 1609.344


@dataclass(frozen=True)
class Comparison:
    """A thing of known length and the lines that compare a route with it."""

    miles: float
    longer: str  # "{dist}" is the thing's length
    times: str  # "{n}" the multiple, "{dist}" the thing's length


COMPARISONS: tuple[Comparison, ...] = (
    Comparison(
        1.7,
        "Longer than the Golden Gate Bridge, which is {dist}.",
        "{n}× the length of the Golden Gate Bridge, which is {dist}.",
    ),
    Comparison(
        2.5,
        "Longer than a lap of the Indianapolis Motor Speedway, which is {dist}.",
        "{n}× a lap of the Indianapolis Motor Speedway, which is {dist}.",
    ),
    Comparison(
        13.4,
        "Longer than Manhattan from end to end, which is {dist}.",
        "{n}× the length of Manhattan, which is {dist}.",
    ),
    Comparison(
        26.2,
        "Longer than the Boston Marathon, which is {dist}.",
        "{n}× the Boston Marathon, which is {dist}.",
    ),
    Comparison(
        72.0,
        "Longer than driving around Lake Tahoe, which is {dist} of shoreline.",
        "{n}× around Lake Tahoe, which is {dist} of shoreline.",
    ),
    Comparison(
        174.0,
        "Longer than driving from Portland to Seattle, which is {dist}.",
        "{n}× the drive from Portland to Seattle, which is {dist}.",
    ),
    Comparison(
        363.0,
        "Longer than the whole Oregon coast on US 101, which is {dist}.",
        "{n}× the Oregon coast on US 101, which is {dist}.",
    ),
    Comparison(
        1381.0,
        "Longer than I-5 from Mexico to Canada, which is {dist}.",
        "{n}× the length of I-5, Mexico to Canada, which is {dist}.",
    ),
    Comparison(
        2448.0,
        "Longer than driving Route 66 end to end, which is {dist}.",
        "{n}× Route 66 end to end, which is {dist}.",
    ),
    Comparison(
        2790.0,
        "Longer than driving from New York to Los Angeles, which is {dist}.",
        "{n}× the drive from New York to Los Angeles, which is {dist}.",
    ),
    Comparison(
        3020.0,
        "Longer than I-90 from Seattle to Boston, which is {dist}.",
        "{n}× the length of I-90, Seattle to Boston, which is {dist}.",
    ),
    Comparison(
        5500.0,
        "Longer than the Great Wall of China, which is {dist}.",
        "{n}× the length of the Great Wall of China, which is {dist}.",
    ),
    Comparison(
        19000.0,
        "Longer than the Pan-American Highway from Alaska to Argentina, which is {dist}.",
        "{n}× the Pan-American Highway, Alaska to Argentina, which is {dist}.",
    ),
    Comparison(
        24901.0,
        "Longer than driving around the Earth, which is {dist}.",
        "{n}× around the Earth, which is {dist}.",
    ),
    Comparison(
        238900.0,
        "Longer than the distance to the Moon, which is {dist}.",
        "{n}× the distance to the Moon, which is {dist}.",
    ),
)

SHORTER = "Shorter than the Golden Gate Bridge, which is {dist}. It still counts."


def comparison_line(length_m: float) -> str:
    """The comparison for a route `length_m` metres long."""
    miles = length_m / METRES_PER_MILE
    exceeded = [c for c in COMPARISONS if miles > c.miles]
    if not exceeded:
        return SHORTER.format(dist=distance_text(COMPARISONS[0].miles))
    thing = exceeded[-1]
    multiple = miles / thing.miles
    if multiple >= 2:
        return thing.times.format(n=f"{multiple:.1f}", dist=distance_text(thing.miles))
    return thing.longer.format(dist=distance_text(thing.miles))


def distance_text(miles: float) -> str:
    """The length as "26.2 mi (42.2 km)": one decimal below 100 (".0" dropped), whole numbers
    from 100 on. The jokes among the directions write their distances so too."""
    return f"{_number(miles)} mi ({_number(miles * METRES_PER_MILE / 1000)} km)"


def _number(value: float) -> str:
    if round(value, 1) < 100:
        return f"{value:.1f}".removesuffix(".0")
    return f"{round(value):,}"
