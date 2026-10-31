"""What a route's document says about it (`docs/reference/presentation.md`, **The document**).

`route_directions(data, nodes, edges, stop_idx, *, labels, seed, ratio, jokes=True)` is a
route's directions as every document and `worst-route directions` give them: its steps
(`directions`), with jokes (`jokes.inject`) drawn from `Generator(PCG64(seed))`, the route's
seed, unless `jokes` is False.
"""

from collections.abc import Sequence

from numpy.random import PCG64, Generator
from numpy.typing import ArrayLike

from worst_route.graph.data import GraphData
from worst_route.presentation.directions import Step, directions
from worst_route.presentation.jokes import inject


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
