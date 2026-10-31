"""What a route's document says about it (`docs/reference/presentation.md`, **The document**).

`present(data, nodes, edges, stop_idx, metrics, *, active, banned, seed, labels, vehicle,
places)` is what both route documents add to a route, the CLI's `route.json` (`worst-route plan
--out`) and the API's `GET /routes/{id}`, from its arrays, its document's metrics, its request's
masks (`active`, `banned`), its seed, its stops' labels, its request's vehicle and the region's
places index:

    {"directions":    [step as JSON, ...],
     "toasts":        [text, ...],
     "metrics_extra": {extra_fuel_gal, extra_cost_usd, states, towns, towns_n,
                       network_fraction}}

`with_presentation(document, presented)` is a `route.json` document with them: `directions` and
`toasts` beside its other keys, `metrics_extra` added to its `metrics`. Nothing of it is stored:
the API works it out again whenever it answers a document, so a route stored earlier gets it too.

`route_directions(data, nodes, edges, stop_idx, *, labels, seed, ratio, jokes=True)` is a
route's directions as every document, `GET /routes/{id}/directions` and `worst-route directions`
give them: its steps (`directions`), with jokes (`jokes.inject`) drawn from
`Generator(PCG64(seed))`, the route's seed, unless `jokes` is False.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.random import PCG64, Generator
from numpy.typing import ArrayLike, NDArray

from worst_route.graph.data import GraphData
from worst_route.presentation.directions import Step, directions
from worst_route.presentation.jokes import inject
from worst_route.presentation.places import PlaceIndex
from worst_route.presentation.stats import Vehicle, stats, toasts


def present(
    data: GraphData,
    nodes: ArrayLike,
    edges: ArrayLike,
    stop_idx: Sequence[int],
    metrics: Mapping[str, Any],
    *,
    active: NDArray[np.bool_],
    banned: NDArray[np.bool_],
    seed: int,
    labels: Sequence[str | None],
    vehicle: Vehicle | None,
    places: PlaceIndex | None,
) -> dict[str, Any]:
    """The route's directions, toasts and extra metrics, as a document adds them."""
    nodes = np.asarray(nodes, dtype=np.int32)
    edges = np.asarray(edges, dtype=np.int32)
    numbers = stats(
        data,
        edges,
        length_m=metrics["length_m"],
        baseline_length_m=metrics["baseline_length_m"],
        vehicle=vehicle,
        places=places,
        active=active,
        banned=banned,
    )
    steps = route_directions(
        data, nodes, edges, stop_idx, labels=labels, seed=seed, ratio=metrics["ratio"]
    )
    return {
        "directions": [s.as_dict() for s in steps],
        "toasts": toasts(numbers, metrics, nodes=nodes, edges=edges),
        "metrics_extra": numbers.metrics(),
    }


def with_presentation(document: Mapping[str, Any], presented: Mapping[str, Any]) -> dict[str, Any]:
    """A `route.json` document with what `present` gave for it."""
    return {
        **document,
        "metrics": {**document["metrics"], **presented["metrics_extra"]},
        "directions": presented["directions"],
        "toasts": presented["toasts"],
    }


def route_directions(
    data: GraphData,
    nodes: ArrayLike,
    edges: ArrayLike,
    stop_idx: Sequence[int],
    *,
    labels: Sequence[str | None],
    seed: int,
    ratio: float | None,
    jokes: bool = True,
) -> list[Step]:
    """The route's steps, with its seed's jokes unless `jokes` is False."""
    steps = directions(data, nodes, edges, stop_idx, labels=labels)
    if not jokes:
        return steps
    return inject(steps, Generator(PCG64(seed)), ratio=ratio)
