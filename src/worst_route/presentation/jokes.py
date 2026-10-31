"""Deadpan jokes among the directions (`docs/reference/presentation.md`, **Jokes**).

`JOKES` are the lines of a GPS that has given up. Some fill placeholders from the route:
`{ratio}` the route's ratio ("42.9×"), `{turns}` how many `turn` steps came before and
`{turn_count}` the same with its noun ("1 turn", "370 turns"), `{distance}` the step just driven
("2.3 mi (3.7 km)", or feet and metres under a tenth of a mile).

`inject(steps, rng, *, ratio)` puts `joke` steps among a route's steps. `rng` is a
`numpy.random.Generator(PCG64(seed))` from the route's seed, so a route and its seed always get
the same jokes. It draws an order of every joke, then the gaps: a joke comes after about every
`JOKE_EVERY`-th driving step (the mean gap is `JOKE_EVERY`, or the driving steps over one more
than the jokes when a route has more of them, so the jokes last the whole way), each gap drawn
uniformly within ±`JOKE_JITTER` of it. A gap's joke is the next in the order that it can fill
(none with `{ratio}` when the ratio is null, a round trip's without a via; none counting turns
before the first turn); no joke is told twice, and once they are all told there are no more. A
joke step drives nothing: distance and time 0, an empty range where the step before it ends.
The steps are numbered again.
"""

import math
from collections.abc import Sequence

import numpy as np

from worst_route.config import JOKE_EVERY, JOKE_JITTER
from worst_route.presentation.comparisons import METRES_PER_MILE, distance_text
from worst_route.presentation.directions import Step, numbered

FEET_PER_METRE = 3.28084

JOKES: tuple[str, ...] = (
    "In 400 feet, reconsider your choices.",
    "You have passed this intersection's neighbour. Not this one. Rules are rules.",
    "Recalculating. No. This is the route.",
    "That was turn {turns}. I have stopped counting. I have not stopped counting.",
    "You just drove {distance}. On purpose.",
    "Your destination is behind you. It has been for some time.",
    "This route is {ratio} worse than it needs to be. You are doing great.",
    "Traffic is light. There is no reason for any of this.",
    "In half a mile, nothing happens.",
    "Keep calm. Keep driving. Keep not arriving.",
    "Stay in the lane that leads away from your destination.",
    "Proceed to the highlighted route. The highlighted route is everything.",
    "Your arrival time has been moved to later. It will be moved again.",
    "Every road is used once. You are using them all.",
    "If you see a sign for your destination, look away.",
    "Continue. Just continue.",
    "The sensible route was {ratio} shorter. It sends its regards.",
    "Please do not turn around. It would repeat an intersection.",
    "You are making excellent progress in the wrong direction.",
    "Somewhere, a shorter route is crying.",
    "Turn number {turns}. Each one was necessary. None of them was necessary.",
    "That stretch was {distance}. It will not be the last.",
    "Your passengers have questions. I do not take questions.",
    "Arriving soon is not on the itinerary.",
    "Lane guidance: any lane. They all go the long way.",
    "This intersection is new to you. So is the next one. So is every one.",
    "You are {ratio} committed to this.",
    "Fuel is your concern. The route is mine.",
    "Note: the destination still exists.",
    "In 1 mile, keep going.",
    "This is the scenic route. The scenery is everything except your destination.",
    "Do not trust the signs. They want you to arrive.",
    "Construction ahead would have been faster.",
    "Satellite signal strong. Judgement weak.",
    "You have now made {turn_count}. A straight line was available.",
    "Speed limit 25. Patience limit exceeded some time ago.",
    "You drove {distance} just now. The map has noted it.",
    "There is a faster route. We are not discussing it.",
    "Make a legal U-turn when possible. It is never possible.",
    "Welcome to a road you have never driven before. Enjoy it. Once.",
)


def inject(steps: Sequence[Step], rng: np.random.Generator, *, ratio: float | None) -> list[Step]:
    """`steps` with jokes after about every `JOKE_EVERY`-th driving step, numbered again."""
    order = [JOKES[k] for k in rng.permutation(len(JOKES)).tolist()]
    told = [j for j in order if ratio is not None or "{ratio}" not in j]
    times = f"{ratio:.1f}×" if ratio is not None else ""
    driving = [k for k, s in enumerate(steps) if s.edge_range[0] < s.edge_range[1]]
    mean = max(JOKE_EVERY, len(driving) / (len(told) + 1))
    low, high = math.floor(mean * (1 - JOKE_JITTER)), math.ceil(mean * (1 + JOKE_JITTER))

    after: dict[int, str] = {}  # step index -> the joke told after it
    at = int(rng.integers(low, high + 1))
    while at <= len(driving) and told:
        k = driving[at - 1]
        turns = sum(s.kind == "turn" for s in steps[: k + 1])
        joke = next((j for j in told if turns > 0 or "{turn" not in j), None)
        if joke is not None:
            told.remove(joke)
            after[k] = joke.format(
                ratio=times,
                turns=turns,
                turn_count=f"{turns:,} turn{'' if turns == 1 else 's'}",
                distance=_distance(steps[k].distance_m),
            )
        at += int(rng.integers(low, high + 1))

    out: list[Step] = []
    for k, step in enumerate(steps):
        out.append(step)
        if k in after:
            end = step.edge_range[1]
            out.append(Step(0, after[k], 0.0, 0.0, (end, end), "joke"))
    return numbered(out)


def _distance(metres: float) -> str:
    """The distance as the comparisons write one (`distance_text`), "2.3 mi (3.7 km)"; under a
    tenth of a mile, "400 ft (120 m)", to the nearest 10."""
    miles = metres / METRES_PER_MILE
    if miles < 0.1:
        return f"{round(metres * FEET_PER_METRE, -1):,.0f} ft ({round(metres, -1):,.0f} m)"
    return distance_text(miles)
