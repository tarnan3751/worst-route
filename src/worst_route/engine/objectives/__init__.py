"""Objectives: what "worse" means (`docs/PLAN.md` §3.6-3.7; `docs/reference/engine.md`).

`base` holds the `Objective` interface and `BaseObjective`; one module per mode (`distance`,
`time`, `collect`, `brand`). `OBJECTIVES` maps a request's objective key to its class, and
`make_objective` builds one for a build, the request's baseline (its `Metrics`) and the radius
bound; `collect` also takes the request's item and its area (the active nodes), `brand` the
request's brand and its area.
"""

from worst_route.engine.objectives.base import BaseObjective, BoolArray, Objective
from worst_route.engine.objectives.brand import Brand
from worst_route.engine.objectives.collect import Collect, CollectItem
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.objectives.time import SlowestTime
from worst_route.engine.route import Metrics
from worst_route.graph.data import GraphData

OBJECTIVES: dict[str, type[BaseObjective]] = {
    "distance": Distance,
    "time": SlowestTime,
    "collect": Collect,
    "brand": Brand,
}


def make_objective(
    key: str,
    data: GraphData,
    *,
    baseline: Metrics,
    r_max: float,
    item: CollectItem | None = None,
    brand: int | None = None,
    area: BoolArray | None = None,
) -> Objective:
    """The objective `key` for this build, baseline and radius bound (and for `collect`, the item
    and the area it collects in; for `brand`, the brand and the area it visits)."""
    if key == "collect":
        if item is None:
            raise ValueError("the collect objective needs an item")
        return Collect(data, item, baseline=baseline, r_max=r_max, area=area)
    if key == "brand":
        if brand is None:
            raise ValueError("the brand objective needs a brand")
        return Brand(data, brand, baseline=baseline, r_max=r_max, area=area)
    return OBJECTIVES[key](data, baseline=baseline, r_max=r_max)
