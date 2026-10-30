"""Settings and named regions.

`Settings` holds what varies per machine, read from the environment: the data directory
(`WORST_ROUTE_DATA`, default `./data`; `CLAUDE.md`, **Commands**) and the User-Agent sent to
outside services. `settings()` returns one cached instance; tests that change the environment
call `settings.cache_clear()`.

`REGIONS` names the areas the pipeline builds (`docs/PLAN.md` §2): each is a Geofabrik state
extract, optionally clipped to a bbox, with the projected CRS its metric steps use. Speeds,
tier definitions, class allowlists and defaults join this module as their steps are built.

`HighwayClass` numbers the road classes a build keeps (stored per edge as `edge_highway`) and
`TIERS` lists each tier's classes (`docs/PLAN.md` §2.6).
"""

from enum import IntEnum
from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Per-machine settings, read from `WORST_ROUTE_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="WORST_ROUTE_", populate_by_name=True)

    data_dir: Path = Field(default=Path("data"), validation_alias="WORST_ROUTE_DATA")
    user_agent: str = "worst-route/0.1.0 (https://github.com/tarnan3751/worst-route)"


class Region(BaseModel):
    """A named build area: a Geofabrik state extract, optionally clipped to a bbox."""

    name: str
    state: str  # Geofabrik slug, e.g. "oregon"
    bbox: tuple[float, float, float, float] | None = None  # (min_lon, min_lat, max_lon, max_lat)
    crs_epsg: int  # projected CRS for the metric steps


REGIONS: dict[str, Region] = {
    "portland": Region(
        name="portland",
        state="oregon",
        bbox=(-123.05, 45.25, -122.35, 45.72),
        crs_epsg=32610,
    ),
    "oregon": Region(name="oregon", state="oregon", crs_epsg=32610),
}


@cache
def settings() -> Settings:
    """The process-wide settings, read from the environment once."""
    return Settings()


# Road classes the pipeline keeps (`docs/PLAN.md` §2.2; `pipeline/exclusions.py` applies them).
# A `highway=service` way is kept only when `service=alley`, as class "alley". A way is dropped
# when its access for cars, by OSM's hierarchy (`motorcar` if present, else `motor_vehicle`, else
# `access`), is in `BLOCKING_ACCESS`.
KEPT_HIGHWAY: frozenset[str] = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
    }
)
ALLEY_SERVICE = "alley"
BLOCKING_ACCESS = frozenset({"private", "no"})


class HighwayClass(IntEnum):
    """The road classes a build keeps, stored per edge as `edge_highway` (uint8).

    Each is the OSM `highway` value of the same name, lowercased, except `ALLEY`, which is
    `highway=service` with `service=alley`.
    """

    MOTORWAY = 0
    MOTORWAY_LINK = 1
    TRUNK = 2
    TRUNK_LINK = 3
    PRIMARY = 4
    PRIMARY_LINK = 5
    SECONDARY = 6
    SECONDARY_LINK = 7
    TERTIARY = 8
    TERTIARY_LINK = 9
    UNCLASSIFIED = 10
    RESIDENTIAL = 11
    LIVING_STREET = 12
    ALLEY = 13


_T0 = frozenset(
    {
        HighwayClass.MOTORWAY,
        HighwayClass.MOTORWAY_LINK,
        HighwayClass.TRUNK,
        HighwayClass.TRUNK_LINK,
    }
)
_T1 = _T0 | {HighwayClass.PRIMARY, HighwayClass.PRIMARY_LINK}
_T2 = _T1 | {HighwayClass.SECONDARY, HighwayClass.SECONDARY_LINK}
_T3 = _T2 | {HighwayClass.TERTIARY, HighwayClass.TERTIARY_LINK}

# The classes of tiers T0..T4; each tier contains the one before it, T4 is every class.
TIERS: tuple[frozenset[HighwayClass], ...] = (_T0, _T1, _T2, _T3, frozenset(HighwayClass))


# The engine (`docs/reference/engine.md`). A request's defaults and the masks it implies: the
# tier is chosen by the straight-line length of the polyline through the stops (T4 below the
# first threshold, T0 beyond the last), the leash is LEASH_FACTOR × that length within
# [LEASH_MIN_KM, LEASH_MAX_KM], and every road within BUBBLE_KM of a stop stays usable. A
# request's own leash is at most LEASH_MAX_KM and its budget at most BUDGET_MAX_S.
DEFAULT_BUDGET_S = 20.0
BUDGET_MAX_S = 120.0
DEFAULT_MAX_ITERATIONS = 5000
TIER_AUTO_KM = (15.0, 60.0, 250.0, 800.0)
LEASH_FACTOR = 0.6
LEASH_MIN_KM = 3.0
LEASH_MAX_KM = 150.0
BUBBLE_KM = 3.0

# The seed route (`docs/PLAN.md` §3.2-3.3): a leg that cannot be built re-snaps a stop up to
# SNAP_FALLBACK_CANDIDATES times, after up to SEED_PENALTY_ROUNDS rounds of rebuilding the
# earlier legs with SEED_PENALTY_FACTOR × the weight on the edges of the nodes they took.
SNAP_FALLBACK_CANDIDATES = 10
SEED_PENALTY_ROUNDS = 4
SEED_PENALTY_FACTOR = 8.0
