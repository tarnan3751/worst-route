"""Objectives: what "worse" means (`docs/PLAN.md` §3.6-3.7; `docs/reference/engine.md`).

`base` holds the `Objective` interface and `BaseObjective`; one module per mode (`distance`,
`time`). `OBJECTIVES` maps a request's objective key to its class, and `make_objective` builds
one for a build, the request's baseline (its `Metrics`) and the radius bound.
"""

from worst_route.engine.objectives.base import BaseObjective, Objective
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.objectives.time import SlowestTime
from worst_route.engine.route import Metrics
from worst_route.graph.data import GraphData

OBJECTIVES: dict[str, type[BaseObjective]] = {"distance": Distance, "time": SlowestTime}


def make_objective(key: str, data: GraphData, *, baseline: Metrics, r_max: float) -> Objective:
    """The objective `key` for this build, baseline and radius bound."""
    return OBJECTIVES[key](data, baseline=baseline, r_max=r_max)
