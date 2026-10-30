"""Which OSM ways the pipeline keeps, and as which road class.

Pure functions over a way's tags, table-driven from `config.KEPT_HIGHWAY`, `ALLEY_SERVICE` and
`BLOCKING_ACCESS` (`docs/PLAN.md` §2.2). A way is kept when its `highway` value is a kept class,
or it is `highway=service` with `service=alley` (class "alley"), unless it is `area=yes` or its
access for cars is in `BLOCKING_ACCESS`. Access follows OSM's hierarchy, the most specific key
present deciding (`ACCESS_KEYS`): `motorcar`, else `motor_vehicle`, else `access`; so
`motor_vehicle=no` + `motorcar=yes` is kept and `access=yes` + `motor_vehicle=no` is dropped.
`filter` applies `is_kept` to every highway way; `build` applies `kept_class` again and stores
the class as the edge's `highway`.
"""

from typing import Protocol

from worst_route.config import ALLEY_SERVICE, BLOCKING_ACCESS, KEPT_HIGHWAY

# A kept road class, as a string: a `KEPT_HIGHWAY` value, or "alley" (`config.HighwayClass`
# numbers the same classes for the on-disk format).
type RoadClass = str

ACCESS_KEYS = ("motorcar", "motor_vehicle", "access")  # most specific first


class Tags(Protocol):
    """A way's tags: a `dict[str, str]`, or pyosmium's `TagList` read in place."""

    def get(self, key: str, /) -> str | None: ...


def kept_class(tags: Tags) -> RoadClass | None:
    """The way's road class if the pipeline keeps it, else None."""
    highway = tags.get("highway")
    if highway is not None and highway in KEPT_HIGHWAY:
        road_class = highway
    elif highway == "service" and tags.get("service") == ALLEY_SERVICE:
        road_class = ALLEY_SERVICE
    else:
        return None
    if tags.get("area") == "yes":
        return None
    access = next((value for key in ACCESS_KEYS if (value := tags.get(key)) is not None), None)
    if access in BLOCKING_ACCESS:
        return None
    return road_class


def is_kept(tags: Tags) -> bool:
    """Whether the pipeline keeps the way."""
    return kept_class(tags) is not None
