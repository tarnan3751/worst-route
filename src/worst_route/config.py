"""Settings and named regions.

`Settings` holds what varies per machine, read from the environment: the data directory
(`WORST_ROUTE_DATA`, default `./data`; `CLAUDE.md`, **Commands**) and the User-Agent sent to
outside services. `settings()` returns one cached instance; tests that change the environment
call `settings.cache_clear()`.

`REGIONS` names the areas the pipeline builds (`docs/PLAN.md` §2): each is a Geofabrik state
extract, optionally clipped to a bbox, with the projected CRS its metric steps use. Speeds,
tier definitions, class allowlists and defaults join this module as their steps are built.

`HighwayClass` numbers the road classes a build keeps (stored per edge as `edge_highway`) and
`TIERS` lists each tier's classes (`docs/PLAN.md` §2.6). `DEFAULT_BACKEND` names the graph
backend the engine searches with unless told otherwise.
"""

from enum import IntEnum
from functools import cache
from pathlib import Path
from typing import Literal

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

# The graph backend (`docs/reference/graph.md`, **Choosing a backend**): "csr" (the Numba
# kernels), "python" (the reference) or "auto", csr when numba imports and python otherwise.
# Both give the same routes; csr is the fast one.
BackendName = Literal["auto", "csr", "python"]
DEFAULT_BACKEND: BackendName = "auto"


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
# A seed leg or a detour's search whose path drives an edge and one of its twins bans the later
# of the two and searches again, up to TWIN_RESEARCH_ROUNDS times (`engine/search.py`).
TWIN_RESEARCH_ROUNDS = 8

# Detours (`docs/PLAN.md` §3.5). A strategy is drawn with STRATEGY_WEIGHTS (waypoint, random
# weight, local exhaustive); local exhaustive only when the window's ends lie less than
# LOCAL_EXHAUSTIVE_MAX_M apart, in a ball of LOCAL_EXHAUSTIVE_BALL_FACTOR × that distance capped
# at LOCAL_EXHAUSTIVE_BALL_MAX_M (the reference backend's DFS grows exponentially with the
# ball). A waypoint is drawn within WAYPOINT_BALL_FACTOR × the radius; searches are bounded by
# SEARCH_BUDGET_FACTOR × the radius. A first radius is RADIUS_INITIAL_FRACTION of the baseline,
# at least RADIUS_INITIAL_MIN_M. A round trip with no stop between its ends starts as a loop
# through a waypoint at least ROUND_TRIP_MIN_M away, drawn up to ROUND_TRIP_SEED_TRIES times.
STRATEGY_WEIGHTS = (0.6, 0.3, 0.1)
RANDOM_WEIGHT_RANGE = (0.2, 5.0)
LOCAL_EXHAUSTIVE_MAX_M = 800.0
LOCAL_EXHAUSTIVE_BALL_FACTOR = 3.0
LOCAL_EXHAUSTIVE_BALL_MAX_M = 400.0
LOCAL_EXHAUSTIVE_MAX_DEPTH = 25
LOCAL_EXHAUSTIVE_MAX_PATHS = 5000
WAYPOINT_BALL_FACTOR = 4.0
SEARCH_BUDGET_FACTOR = 4.0
RADIUS_INITIAL_FRACTION = 0.25
RADIUS_INITIAL_MIN_M = 1000.0
ROUND_TRIP_MIN_M = 500.0
ROUND_TRIP_SEED_TRIES = 20

# The worsen loop (`docs/PLAN.md` §3.4). A window is one edge with SINGLE_EDGE_PROBABILITY, else
# a run of RUN_EDGES edges. The radius grows RADIUS_GROWTH × on every accepted splice (up to the
# leash or the active area's extent) and halves after every RADIUS_HALVE_EVERY failures in a row
# (down to RADIUS_MIN_M); more than CONVERGED_FAILS failures in a row at the floor end the run.
SINGLE_EDGE_PROBABILITY = 0.8
RUN_EDGES = (2, 4)
RADIUS_GROWTH = 1.5
RADIUS_HALVE_EVERY = 20
RADIUS_MIN_M = 300.0
CONVERGED_FAILS = 400

# Cleanup (`docs/PLAN.md` §2.4; `docs/reference/pipeline.md`, cleanup).
# Stage A (`pipeline/consolidate.py`): the radius OSMnx buffers each node by; two nodes merge
# when their buffers overlap and an edge of at most STAGE_A_MAX_LENGTH_M joins them. The first
# rule that matches a node's incident edges wins: a roundabout edge (stage B owns roundabouts),
# then an arterial class (medians of divided arterials are 20-30 m wide), else the default.
STAGE_A_TOLERANCE_ROUNDABOUT_M = 1.0
STAGE_A_TOLERANCE_ARTERIAL_M = 15.0
STAGE_A_TOLERANCE_M = 8.0
STAGE_A_ARTERIAL_CLASSES = frozenset({"trunk", "primary", "secondary"})
STAGE_A_MAX_LENGTH_M = 60.0
# Stage B (`pipeline/consolidate.py`): ramps (`*_link`) and roundabouts become super-junctions.
# Ramp components whose attachment roads share two identities and lie within STAGE_B_JOIN_M of
# each other are one interchange; a cluster whose links are all minor (STAGE_B_MINOR_LINKS)
# takes in the at-grade node its slip lanes bypass, a neighbour of two of its attachment nodes
# through two different roads within STAGE_B_SLIP_CENTER_M. A cluster then takes in the nodes
# of a pocket: a path of at most STAGE_B_POCKET_M along one road leaving the cluster and coming
# back to it (a bridge between ramp terminals). A cluster wider than STAGE_B_MAX_DIAMETER_M
# (bbox diagonal) is split by single linkage of its attachment nodes at STAGE_B_SPLIT_EPS_M.
# Roundabouts never split.
STAGE_B_JOIN_M = 500.0
STAGE_B_MINOR_LINKS = frozenset({"primary_link", "secondary_link", "tertiary_link"})
STAGE_B_SLIP_CENTER_M = 150.0
STAGE_B_POCKET_M = 600.0
STAGE_B_MAX_DIAMETER_M = 1500.0
STAGE_B_SPLIT_EPS_M = 300.0
# Carriageway twins (`pipeline/carriageways.py`): one-way edges of TWIN_CLASSES with the same
# ref set (else name) whose segments run in opposite directions (180° ± TWIN_BEARING_TOL) within
# the lateral distance of their class. Edges with neither ref nor name pair only in
# TWIN_GEOMETRY_ONLY_CLASSES.
TWIN_CLASSES = frozenset({"motorway", "trunk", "primary", "secondary", "tertiary"})
TWIN_GEOMETRY_ONLY_CLASSES = frozenset({"motorway", "trunk"})
TWIN_LATERAL_M = {
    "motorway": 100.0,
    "trunk": 80.0,
    "primary": 50.0,
    "secondary": 35.0,
    "tertiary": 35.0,
}
TWIN_BEARING_TOL = 30.0
# Connectivity (`pipeline/connectivity.py`): cleanup fails when the largest strongly connected
# component keeps fewer than this fraction of the nodes, unless --allow-fragmented.
SCC_MIN_RETAINED = 0.99

# Enrich (`docs/PLAN.md` §2.5; `docs/reference/pipeline.md`, enrich). An edge's speed is its
# `maxspeed` tag when it parses, else its class's default here, in mph; a node's delay is the
# largest NODE_DELAY_S of the stop signs and signals merged into it; an edge's bearings are taken
# over the first and the last BEARING_SAMPLE_M of its geometry.
SPEED_DEFAULT_MPH: dict[HighwayClass, float] = {
    HighwayClass.MOTORWAY: 65.0,
    HighwayClass.MOTORWAY_LINK: 35.0,
    HighwayClass.TRUNK: 55.0,
    HighwayClass.TRUNK_LINK: 35.0,
    HighwayClass.PRIMARY: 45.0,
    HighwayClass.PRIMARY_LINK: 35.0,
    HighwayClass.SECONDARY: 35.0,
    HighwayClass.SECONDARY_LINK: 35.0,
    HighwayClass.TERTIARY: 30.0,
    HighwayClass.TERTIARY_LINK: 35.0,
    HighwayClass.UNCLASSIFIED: 25.0,
    HighwayClass.RESIDENTIAL: 25.0,
    HighwayClass.LIVING_STREET: 15.0,
    HighwayClass.ALLEY: 10.0,
}
NODE_DELAY_S = {"stop": 10.0, "traffic_signals": 30.0}
BEARING_SAMPLE_M = 20.0

# Tiers (`docs/PLAN.md` §2.6; `pipeline/tiers.py`): each tier keeps the largest strongly
# connected component of its own roads; the step fails when a tier keeps fewer than this
# fraction of the nodes its roads touch, unless --allow-fragmented.
TIER_MIN_RETAINED = 0.95

# QA (`docs/PLAN.md` §2.8; `pipeline/qa.py`): a build is accepted when twins cover at least this
# share of one-way motorway and trunk length, cleanup kept SCC_MIN_RETAINED of the nodes, and
# every tier TIER_MIN_RETAINED of its own.
QA_TWIN_COVERAGE_MIN = 0.95
