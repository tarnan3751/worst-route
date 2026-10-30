"""Snapping a request's stops to nodes (`docs/PLAN.md` §3.2; `docs/reference/engine.md`, **Stops**).

`snap_stops(spatial, data, stops, active_mask)` snaps each stop with `SpatialIndex.snap` in the
request's active mask, by role: the first stop is the start, the last the end, the others via
stops (which must be passable without turning back; a cul-de-sac moves to the nearest through
street, with the note `snap` gives). Stops stay on distinct nodes: a via avoids the nodes of
the stops before it, and the end those of every other stop. The one exception is a round trip,
a request whose start and end points are identical (`is_round_trip`): its end may take the
start's node. A stop moved off another stop's node says so in its note.

`resnap(...)` is the fallback when a leg cannot be built (`seed_route.resolve_stops` drives it):
the stop is snapped again with the nodes already tried excluded, and its note says why it
moved ("without repeating one", or "under the strict no-left constraint"). `StopUnreachable`
names the leg (k, k + 1) no route covers without repeating a road; its `stop_index` is the leg's
later stop, the stop the API's error names. `InfeasibleTurnConstraint`, a `StopUnreachable`,
says a strict turn constraint is why (`constraint`: its key).
"""

from collections.abc import Sequence
from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from worst_route.graph.data import GraphData
from worst_route.graph.spatial import Role, Snap, SpatialIndex


class StopUnreachable(Exception):  # noqa: N818 (the name the design uses)
    """No route covers the leg `leg` without repeating a road; `stop_index` is its later stop."""

    def __init__(self, leg: tuple[int, int], detail: str = "") -> None:
        reason = f", {detail}" if detail else ""
        start, end = leg
        super().__init__(
            f"no route from stop {start} to stop {end} without repeating a road{reason}"
        )
        self.leg = leg
        self.stop_index = end


class InfeasibleTurnConstraint(StopUnreachable):
    """A leg no route covers without repeating a road under a strict turn constraint;
    `constraint` is its key, `"no_left"` or `"no_right"`."""

    def __init__(self, leg: tuple[int, int], detail: str, constraint: str) -> None:
        super().__init__(leg, detail)
        self.constraint = constraint


def role_of(k: int, count: int) -> Role:
    """The role of stop k of `count`."""
    return "start" if k == 0 else "end" if k == count - 1 else "via"


def is_round_trip(stops: Sequence[tuple[float, float]]) -> bool:
    """Whether a request's start and end points are identical."""
    return len(stops) >= 2 and tuple(stops[0]) == tuple(stops[-1])


def apart_from(snaps: Sequence[Snap], k: int, count: int, round_trip: bool) -> list[int]:
    """The nodes stop k may not take: the other stops' (a round trip's end may take the start's)."""
    shared = round_trip and role_of(k, count) == "end"
    return [s.node for j, s in enumerate(snaps) if j != k and not (shared and j == 0)]


def snap_stops(
    spatial: SpatialIndex,
    data: GraphData,
    stops: Sequence[tuple[float, float]],
    active_mask: NDArray[np.bool_],
) -> list[Snap]:
    """Each stop's node, in order, by role and apart from the other stops."""
    snaps: list[Snap] = []
    round_trip = is_round_trip(stops)
    for k, (lat, lon) in enumerate(stops):
        role = role_of(k, len(stops))
        snap = spatial.snap(lat, lon, role, active_mask)
        others = apart_from(snaps, k, len(stops), round_trip)
        if snap.node in others:
            exclude = np.zeros(data.n, dtype=np.bool_)
            exclude[others] = True
            moved = spatial.snap(lat, lon, role, active_mask, exclude=exclude)
            snap = replace(
                moved, note=f"moved {moved.moved_m:.0f} m: another stop is at the nearest node"
            )
        snaps.append(snap)
    return snaps


def resnap(
    spatial: SpatialIndex,
    stop: tuple[float, float],
    role: Role,
    active_mask: NDArray[np.bool_],
    exclude: NDArray[np.bool_],
    reason: str = "without repeating one",
) -> Snap:
    """The stop's nearest node outside `exclude`, noted as moved because nearer ones could not
    be reached (`reason`: without repeating one, or under a strict turn constraint)."""
    snap = spatial.snap(stop[0], stop[1], role, active_mask, exclude=exclude)
    return replace(
        snap, note=f"moved {snap.moved_m:.0f} m: nearer nodes cannot be reached {reason}"
    )
