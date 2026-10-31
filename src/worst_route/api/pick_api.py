"""Which served build a trip runs on (`docs/reference/api.md`, **Picking a build**).

    POST /regions/pick    {stops: [{lat, lon}, ...]} -> {region, build_id, reason}

The pick is the smallest served region that holds every stop, among the regions whose bbox holds
every stop (`routes_api.holds`, the bbox `POST /jobs` checks a stop against, `Loaded.bbox`). A
stop's state is the places index's (`PlaceIndex.states_at`: every index holds the same state
polygons); a stop at sea or abroad is in none. First a region clipped to a bbox
(`config.Region.bbox`, as portland is), the smallest bbox first, which with a places index loaded
must also name every stop's state among its own (`config.Region.fips`: Portland → Vancouver, WA, is
not portland's); a region `config.REGIONS` does not name goes by its bbox alone. Then a region of
whole states whose states hold every stop's state, the fewest states first; when a region of fewer
states holds them but its bbox misses a stop, the reason says so ("all stops in Oregon, one beyond
Oregon's build"). A stop in no state, or a server without any places index, leaves no region of
whole states holding the stops. Failing those, the region with the most states among those whose
bbox holds every stop (the national one), else 422 `STOP_OUTSIDE_REGION` with the first stop
outside the region whose bbox holds the most stops (`stop_index`). A tie goes to the region served
first. `reason` says why in a few words, for the UI: "all stops in Oregon", "stops in Oregon and
Illinois", "stops in 3 states", "all stops within Portland", "a stop in no state". On a public
server a client picks at most `PUBLIC_PICK_PER_MINUTE` times in any minute (429 `RATE_LIMITED`, +
`retry_after_s`, as for the place search). `POST /jobs` still names its region: the client sends
the one picked.
"""

from typing import TYPE_CHECKING

import numpy as np
from fastapi import APIRouter, Request
from numpy.typing import NDArray

from worst_route.api.limits import client_of
from worst_route.api.routes_api import ApiError, api_of, bbox_of, display_of, holds
from worst_route.api.schemas import RegionPickIn, RegionPickOut
from worst_route.config import REGIONS
from worst_route.presentation.places import State

if TYPE_CHECKING:
    from worst_route.api.app import Api

router = APIRouter()


@router.post("/regions/pick")
def pick(body: RegionPickIn, request: Request) -> RegionPickOut:
    """The served region a trip with these stops runs on, and why."""
    api = api_of(request)
    if api.pick_quota is not None:
        wait = api.pick_quota.take(client_of(request.scope, api.settings.trust_proxy))
        if wait is not None:
            message = f"at most {api.pick_quota.per_minute} picks a minute; try again in {wait} s"
            raise ApiError(429, "RATE_LIMITED", message, retry_after_s=wait)
    region, reason = pick_region(api, [(s.lat, s.lon) for s in body.stops])
    build_id = str(api.builds[region].data.meta["build_id"])
    return RegionPickOut(region=region, build_id=build_id, reason=reason)


def pick_region(api: "Api", stops: list[tuple[float, float]]) -> tuple[str, str]:
    """The region for `stops` ((lat, lon) each) and why; STOP_OUTSIDE_REGION when none holds
    them."""
    holding = [r for r, b in api.builds.items() if all(holds(b, lat, lon) for lat, lon in stops)]
    if not holding:
        raise _outside(api, stops)
    index = next((i for i in api.places.values() if i is not None), None)
    found = [] if index is None else index.states_at(_lon_lat(stops))
    states = [s for s in found if s is not None]
    fips = {s.fips for s in states} if len(states) == len(stops) else None
    clipped = [r for r in holding if _clipped(r) and (index is None or _named(r, fips))]
    if clipped:
        best = min(clipped, key=lambda r: _area(bbox_of(api.builds[r])))
        return best, f"all stops within {display_of(best)}"
    whole = [r for r in holding if _whole(r) and fips is not None and fips <= REGIONS[r].fips]
    if fips is not None and whole:
        best = min(whole, key=lambda r: len(REGIONS[r].fips))
        return best, _in_states(states) + _beyond(api, best, fips, stops)
    best = max(holding, key=lambda r: len(REGIONS[r].fips) if r in REGIONS else 0)
    if index is None:
        return best, "no places index: by bounding box"
    if fips is None:
        return best, "a stop in no state"
    return best, f"{_in_states(states)}, by bounding box"


def _whole(region: str) -> bool:
    """Whether `region` is one of whole states (config names it, unclipped)."""
    known = REGIONS.get(region)
    return known is not None and known.bbox is None


def _clipped(region: str) -> bool:
    """Whether `region` is clipped to a bbox, or one config does not name (its bbox is all)."""
    known = REGIONS.get(region)
    return known is None or known.bbox is not None


def _named(region: str, fips: set[str] | None) -> bool:
    """Whether every stop's state (`fips`, None when a stop is in none) is among the states
    `region` names; a region config does not name names none and asks none."""
    known = REGIONS.get(region)
    return known is None or (fips is not None and fips <= known.fips)


def _beyond(api: "Api", best: str, fips: set[str], stops: list[tuple[float, float]]) -> str:
    """ ", one beyond Oregon's build" when a served region of fewer states than `best` holds the
    stops' states but its bbox misses some of them, else ""."""
    fewer = [
        r
        for r in api.builds
        if _whole(r) and fips <= REGIONS[r].fips and len(REGIONS[r].fips) < len(REGIONS[best].fips)
    ]
    if not fewer:
        return ""
    smaller = min(fewer, key=lambda r: len(REGIONS[r].fips))
    out = sum(not holds(api.builds[smaller], lat, lon) for lat, lon in stops)
    return f", {'one' if out == 1 else out} beyond {display_of(smaller)}'s build"


def _lon_lat(stops: list[tuple[float, float]]) -> NDArray[np.float64]:
    return np.array([(lon, lat) for lat, lon in stops], dtype=np.float64)


def _area(bbox: tuple[float, float, float, float]) -> float:
    west, south, east, north = bbox
    return (east - west) * (north - south)


def _in_states(states: list[State]) -> str:
    """The stops' states, in the order the stops first enter them."""
    names = list(dict.fromkeys(s.name for s in states))
    if len(names) == 1:
        return f"all stops in {names[0]}"
    if len(names) == 2:
        return f"stops in {names[0]} and {names[1]}"
    return f"stops in {len(names)} states"


def _outside(api: "Api", stops: list[tuple[float, float]]) -> ApiError:
    """STOP_OUTSIDE_REGION for the first stop outside the region whose bbox holds the most."""
    held = {r: [holds(b, lat, lon) for lat, lon in stops] for r, b in api.builds.items()}
    most = max(held, key=lambda r: sum(held[r]))
    k = held[most].index(False)
    lat, lon = stops[k]
    if not any(inside[k] for inside in held.values()):
        served = ", ".join(api.builds)
        message = f"stop {k} ({lat}, {lon}) is outside every region served here ({served})"
    else:
        message = (
            f"no region served here holds every stop: stop {k} ({lat}, {lon}) is outside "
            f"{most}, which holds the most of them"
        )
    return ApiError(422, "STOP_OUTSIDE_REGION", message, stop_index=k)
