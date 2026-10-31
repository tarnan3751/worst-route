"""The order in which networkx iterates a `MultiDiGraph`'s edges, for arrays to reproduce.

A `MultiDiGraph` holds each node's out-edges as a dict of neighbours, each a dict of edges, both
in insertion order: its edges come by tail (in the order the nodes were added), then by
neighbour, in the order the first edge to that neighbour was inserted, then by their own
insertion. `iteration_order` sorts arrays of edges so, given each edge's insertion as keys
(several arrays, most significant first, compared as a tuple). The steps after the arrays
engine's depend on this order: their tie-breaks, and the keys networkx gives parallel edges.
"""

from typing import Any

import numpy as np
from numpy.typing import NDArray


def iteration_order(
    position: NDArray[Any], head: NDArray[Any], *insertion: NDArray[Any]
) -> NDArray[np.intp]:
    """The permutation of the edges that networkx iterates: `position` is each edge's tail's
    place among the nodes, `head` its head, `insertion` when it was inserted. An edge between two
    nodes that was inserted and removed before the others never counts, so the earliest edge
    still there stands for its neighbour's entry."""
    by_entry = np.lexsort((*reversed(insertion), head, position))
    pair = np.column_stack([position[by_entry], head[by_entry]])
    new = np.ones(len(by_entry), dtype=np.bool_)
    new[1:] = np.any(pair[1:] != pair[:-1], axis=1)
    lead = by_entry[np.flatnonzero(new)][np.cumsum(new) - 1]  # each edge's entry's first edge
    entry = [np.empty_like(key) for key in insertion]
    for key, first in zip(insertion, entry, strict=True):
        first[by_entry] = key[lead]
    return np.lexsort((*reversed(insertion), *reversed(entry), position))
