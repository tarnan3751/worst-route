"""Objectives: what "worse" means (`docs/PLAN.md` §3.6-3.7; `docs/reference/engine.md`).

`base` holds the `Objective` interface and `BaseObjective`; one module per mode (`distance`,
`time`, `collect`). `OBJECTIVES` maps a request's objective key to its class, and
`make_objective` builds one for a build, the request's baseline (its `Metrics`) and the radius
bound; `collect` also takes the request's item and its area (the active nodes).
"""

from worst_route.engine.objectives.base import BaseObjective, BoolArray, Objective
from worst_route.engine.objectives.collect import Collect, CollectItem
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.objectives.time import SlowestTime
from worst_route.engine.route import Metrics
from worst_route.graph.data import GraphData

OBJECTIVES: dict[str, type[BaseObjective]] = {
    "distance": Distance,
    "time": SlowestTime,
    "collect": Collect,
}


def make_objective(
    key: str,
    data: GraphData,
    *,
    baseline: Metrics,
    r_max: float,
    item: CollectItem | None = None,
    area: BoolArray | None = None,
) -> Objective:
    """The objective `key` for this build, baseline and radius bound (and for `collect`, the item
    and the area it collects in)."""
    if key == "collect":
        if item is None:
            raise ValueError("the collect objective needs an item")
        return Collect(data, item, baseline=baseline, r_max=r_max, area=area)
    return OBJECTIVES[key](data, baseline=baseline, r_max=r_max)
