"""A route's extra numbers and its toasts (`docs/reference/presentation.md`, **Stats and
toasts**).

`stats(data, edges, *, length_m, baseline_length_m, vehicle, places, geometry, active, banned)`
works out:

- `extra_fuel_gal`, the length beyond the baseline's in miles over the vehicle's `mpg`, and
  `extra_cost_usd`, that at its `gas_price_usd`; both None without a vehicle;
- `states` and `towns`, the Census names (`NAME`, "Portland", not "Portland city") of the states
  and towns the route passes through, in the order it first enters them
  (`PlaceIndex.states_crossed` and `towns_passed` on `route_line`, along the build's
  `geometry`, which only they need), and `towns_n`, how many towns; all three None without a
  places index;
- `network_fraction`, the road units (`edge_unit`) the route drives over those of every edge
  its request lets it drive: both ends in the request's `active` mask, not in its `banned` one.

`Stats.metrics()` gives them as a route document's metrics carry them.

`toasts(stats, metrics, *, nodes, edges)` gives at most `TOASTS_MAX` one-line deadpan facts from
them and the document's metrics (the ratio, the times and delays, the turns, the items when the
route collects), the most striking first, in a fixed order: the ratio; a round trip's ending
where it began; the towns; never turning one way; the fuel; the time; the delays; the
intersections, none twice; the stretches of road, each once; the items. A fact the stat line
already gives as it stands (the states, the turns both ways, the share of the roads within
reach) is no toast. A fact a route does not have (no towns without an index, no fuel without a
vehicle, no ratio for a round trip without a via, whose baseline is 0 m) is left out, so the
cap is never reached. No randomness: the same route always gets the same toasts.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from worst_route.config import TOASTS_MAX
from worst_route.engine.route import IntArray
from worst_route.graph.data import GraphData
from worst_route.graph.geometry import Geometry
from worst_route.presentation.comparisons import METRES_PER_MILE
from worst_route.presentation.places import PlaceIndex, route_line

ITEM_WORDS = {
    "toll_booth": ("toll booth", "toll booths"),
    "level_crossing": ("level crossing", "level crossings"),
    "roundabout": ("roundabout", "roundabouts"),
    "bridge": ("bridge", "bridges"),
}


class Vehicle(Protocol):
    """The car of a request (the API's `vehicle`)."""

    @property
    def mpg(self) -> float: ...

    @property
    def gas_price_usd(self) -> float: ...


@dataclass(frozen=True)
class Stats:
    """A route's numbers beyond its metrics; None where they cannot be known."""

    extra_fuel_gal: float | None
    extra_cost_usd: float | None
    states: list[str] | None
    towns: list[str] | None
    towns_n: int | None
    network_fraction: float

    def metrics(self) -> dict[str, Any]:
        """The numbers as a route document's metrics carry them."""
        return {
            "extra_fuel_gal": self.extra_fuel_gal,
            "extra_cost_usd": self.extra_cost_usd,
            "states": self.states,
            "towns": self.towns,
            "towns_n": self.towns_n,
            "network_fraction": self.network_fraction,
        }


def stats(
    data: GraphData,
    edges: IntArray,
    *,
    length_m: float,
    baseline_length_m: float,
    vehicle: Vehicle | None,
    places: PlaceIndex | None,
    geometry: Geometry | None,
    active: NDArray[np.bool_],
    banned: NDArray[np.bool_],
) -> Stats:
    """The route's extra fuel and cost, its states and towns, and its share of the roads."""
    fuel = cost = None
    if vehicle is not None:
        fuel = (length_m - baseline_length_m) / METRES_PER_MILE / vehicle.mpg
        cost = fuel * vehicle.gas_price_usd
    states = towns = None
    if places is not None:
        if geometry is None:
            raise ValueError("stats: a places index needs the build's geometry")
        line = route_line(data, geometry, edges)
        states = [s.name for s in places.states_crossed(line)]
        towns = [t.name for t in places.towns_passed(line)]
    usable = active[data.edge_u] & active[data.edge_v] & ~banned
    within = np.unique(data.edge_unit[usable]).size
    driven = np.unique(data.edge_unit[edges]).size
    return Stats(
        extra_fuel_gal=fuel,
        extra_cost_usd=cost,
        states=states,
        towns=towns,
        towns_n=len(towns) if towns is not None else None,
        network_fraction=driven / within,
    )


def toasts(
    stats: Stats, metrics: Mapping[str, Any], *, nodes: IntArray, edges: IntArray
) -> list[str]:
    """The route's toasts, the most striking first."""
    round_trip = bool(nodes[0] == nodes[-1])
    turns = metrics["turns"]
    left, right = int(turns["left"]), int(turns["right"])
    ratio = metrics["ratio"]
    experienced = metrics["time_s"] + metrics["delay_s"]
    sensible = metrics["baseline_time_s"] + metrics["baseline_delay_s"]
    lines: list[str | None] = [
        f"{ratio:.1f}× longer than it had to be." if ratio is not None else None,
        "Ends exactly where it began." if round_trip else None,
        _towns(stats.towns),
        f"Never turns left. {_count(right, 'right turn')}." if left == 0 and right > 0 else None,
        f"Never turns right. {_count(left, 'left turn')}." if right == 0 and left > 0 else None,
        f"An extra {stats.extra_fuel_gal:.1f} gallons, ${stats.extra_cost_usd:,.2f}, for nothing."
        if stats.extra_fuel_gal is not None and stats.extra_cost_usd is not None
        else None,
        f"{_duration(experienced)} on the road instead of {_duration(sensible)}."
        if sensible > 0
        else f"{_duration(experienced)} on the road to get nowhere.",
        f"{_duration(metrics['delay_s'])} waiting at signals and stop signs."
        if metrics["delay_s"] > 0
        else None,
        f"{_count(len(nodes) - 1 if round_trip else len(nodes), 'intersection')}, "
        "none of them twice.",
        f"{_count(len(edges), 'stretch', 'stretches')} of road, each driven exactly once.",
        _items(metrics.get("items")),
    ]
    return [line for line in lines if line is not None][:TOASTS_MAX]


def _towns(towns: list[str] | None) -> str | None:
    if not towns:
        return None
    if len(towns) == 1:
        return f"Never leaves {towns[0]}. Sees all of it instead."
    return f"Passes through {len(towns)} towns. {towns[0]} twice."


def _items(items: Mapping[str, int] | None) -> str | None:
    counted = [_count(n, *ITEM_WORDS[name]) for name, n in (items or {}).items() if n > 0]
    return f"Passes {_listed(counted)} on the way." if counted else None


def _count(n: int, one: str, many: str | None = None) -> str:
    """The count and its noun: "1 left turn", "2,013 left turns"."""
    return f"{n:,} {one if n == 1 else many or one + 's'}"


def _listed(words: list[str]) -> str:
    """The words as a list: "a", "a and b", "a, b and c"."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _duration(seconds: float) -> str:
    """The time in whole minutes, as `worst-route plan` counts them: "10 h 54 min", "21 min",
    "45 s"."""
    minutes, secs = divmod(round(seconds), 60)
    if minutes == 0:
        return f"{secs} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"
