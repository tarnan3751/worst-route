"""Settings and named regions.

`Settings` holds what varies per machine, read from the environment: the data directory
(`WORST_ROUTE_DATA`, default `./data`; `CLAUDE.md`, **Commands**), the User-Agent sent to
outside services, the contact (`WORST_ROUTE_CONTACT`) the geocoder adds to it, and the basemap
the API offers the frontend (`WORST_ROUTE_BASEMAP`, `raster` or `pmtiles`;
`docs/reference/hosting.md`).
`settings()` returns one cached instance; tests that change the environment call
`settings.cache_clear()`.

`REGIONS` names the areas the pipeline builds (`docs/PLAN.md` §2): each is one or more Geofabrik
state extracts, read in order and merged, optionally clipped to a bbox and limited to a tier's road
classes, with the projected CRS its metric steps use (the regions of several states follow `STATES`,
and so does a region for each contiguous state) and the name the frontend shows (`display`);
`PIPELINE_VERSION` is the version every build id and sidecar records. Speeds, tier definitions,
class allowlists and defaults join this module as their steps are built.

`HighwayClass` numbers the road classes a build keeps (stored per edge as `edge_highway`) and
`TIERS` lists each tier's classes (`docs/PLAN.md` §2.6). `DEFAULT_BACKEND` names the graph
backend the engine searches with unless told otherwise. The `API_*` numbers are the API's job
limits and streaming cadence (`docs/reference/api.md`); the `GEOCODE_*` numbers its geocoding
proxy's rate, cache and limits; the `PUBLIC_*` numbers the limits of a public server.
"""

import math
import os
from enum import IntEnum
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The basemap the API offers the frontend: OpenStreetMap's raster tiles (hobby volume, local
# use) or Protomaps' vector tiles from a file the server holds (`api/basemap_api.py`).
BasemapName = Literal["raster", "pmtiles"]


class Settings(BaseSettings):
    """Per-machine settings, read from `WORST_ROUTE_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="WORST_ROUTE_", populate_by_name=True)

    data_dir: Path = Field(default=Path("data"), validation_alias="WORST_ROUTE_DATA")
    user_agent: str = "worst-route/0.1.0 (https://github.com/tarnan3751/worst-route)"
    contact: str | None = None  # WORST_ROUTE_CONTACT: added to the geocoder's User-Agent
    basemap: BasemapName = "raster"  # WORST_ROUTE_BASEMAP: OSM's raster tiles, or pmtiles


def display_name(slug: str) -> str:
    """A slug as words, each capitalised but "of" and "and" after the first: "district-of-columbia"
    is "District of Columbia"."""
    words = slug.split("-")
    return " ".join(w if k and w in ("of", "and") else w.capitalize() for k, w in enumerate(words))


class Region(BaseModel):
    """A named build area: Geofabrik state extracts, merged, optionally clipped to a bbox."""

    name: str
    # What the frontend calls it ("Oregon", "United States"; `GET /regions`' `name`): by default
    # its name as words (`display_name`).
    display: str = Field(default_factory=lambda data: display_name(data["name"]))
    # Geofabrik slugs, e.g. ("oregon",), read in this order: a way two extracts hold (one crossing
    # their border) is taken from the first.
    states: tuple[str, ...] = Field(min_length=1)
    bbox: tuple[float, float, float, float] | None = None  # (min_lon, min_lat, max_lon, max_lat)
    crs_epsg: int  # projected CRS for the metric steps
    # A ceiling: only the road classes of this tier (`TIERS`) are kept, from `filter` on.
    classes: Literal["T0", "T1", "T2", "T3"] | None = None

    @property
    def road_classes(self) -> frozenset[str] | None:
        """The road classes (`exclusions.RoadClass`) the ceiling keeps; None without one."""
        if self.classes is None:
            return None
        return frozenset(c.name.lower() for c in TIERS[int(self.classes[1])])

    @property
    def fips(self) -> frozenset[str]:
        """The FIPS codes of its states (`STATES`), which the API's places and picks go by."""
        return frozenset(STATES[state][1] for state in self.states)


REGIONS: dict[str, Region] = {
    "portland": Region(
        name="portland",
        display="Portland",
        states=("oregon",),
        bbox=(-123.05, 45.25, -122.35, 45.72),
        crs_epsg=32610,
    ),
    "oregon": Region(name="oregon", states=("oregon",), crs_epsg=32610),
}

# The pipeline's version enters every build id and every step's sidecar (`pipeline/layout.py`);
# bump it when a step's output changes for the same input. `pipeline` re-exports it, and
# `graph.store` reads it to name the remedy for a build another version wrote. 2: bearings
# exclude stubs, node_roundabouts; 3: brand arrays; 4: stage A places each cluster from its
# own buffers; 5: stage B joins the clusters a carriageway leaves and comes back to, and its
# pockets follow a road by its name as well as its refs; 6: the compact format (graph/data.py
# FORMAT_VERSION 4); 7: one road unit per OSM node run however many ways map it; simplify keeps
# the shared node of two one-way ways overlapping in part.
PIPELINE_VERSION = "7"
# The pipeline's engine (`docs/reference/pipeline.md`, **The arrays engine**): "arrays" (NumPy,
# scipy, shapely and Numba), the default, or "networkx" (networkx and OSMnx), for `pipeline build`
# and `pipeline cleanup`. Both make the same build, byte for byte.
PipelineEngine = Literal["networkx", "arrays"]
DEFAULT_PIPELINE_ENGINE: PipelineEngine = "arrays"


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
# [LEASH_MIN_KM, LEASH_MAX_KM], or LEASH_LONG_FACTOR × it when that is more (from 750 km), and
# every road within BUBBLE_KM of a stop stays usable. A request's own leash is at most
# LEASH_MAX_KM and its budget at most BUDGET_MAX_S.
DEFAULT_BUDGET_S = 20.0
BUDGET_MAX_S = 120.0
DEFAULT_MAX_ITERATIONS = 5000
# TIER_AUTO_KM: T4 below 800 km, T0 from 800 km, T1-T3 by override only. Tuned on the Oregon
# build (M5.1, docs/reference/engine.md, **Requests**): at every trip length a state holds, the
# all-roads graph gave the best routes in 30 s and reached every stop the sparser tiers did, and
# more: four named trips of 166-592 km made 3.56-10.99x on T4 against 1.32-5.31x on T2 and
# 3.35-6.73x on T3, ten random pairs of 71-403 km planned 8 of 10 on T4 (6.10-10.85x), 7 on T3,
# 4 on T2 (a rural stop's 3 km bubble seldom meets a sparser tier's roads), and T1 refused two
# trips over I-5. Before: (15, 60, 250, 800), T4, T3, T2, T1 below each, T0 beyond. From 800 km
# T1, and T0 by override only (engine version 5), tuned on the national T1 build (c4a85b3d36d0,
# docs/reference/engine.md, **On the national T1 build**): Portland to Chicago, New York to Los
# Angeles, Chicago to Miami and Miami to Seattle made 1.0-2.8x in 60 s on T0 at every leash
# tried (150 km, a tenth and a fifth of the trip), the motorways and trunks holding few loops a
# detour can take, against 8.6-11.1x on T1 with a fifth of the trip as leash (the plan's runs,
# windows widened by its prototype; 6.19-10.26x as built); at 150 km both refused Miami to
# Seattle and T0 Portland to Chicago. That build holds T1's roads only, so on an
# all-roads national build T1 from 800 km, and whether a rural stop's 3 km bubble meets its
# roads, is untested (on Oregon a stop in the Ochoco National Forest lies 7.1 km from T1 or T2).
TIER_AUTO_KM = (800.0, 800.0, 800.0, math.inf)
LEASH_FACTOR = 0.6
LEASH_MIN_KM = 3.0
LEASH_MAX_KM = 150.0
# A fifth of the trip, past LEASH_MAX_KM from 750 km: on the national T1 build at 150 km (3% of
# a 4,500 km trip) the four trips above made 3.4-6.3x on T1 and Miami to Seattle was refused, at
# a tenth of the trip 4.9-8.6x, at a fifth 8.6-11.1x (the plan's runs, as above).
LEASH_LONG_FACTOR = 0.2
BUBBLE_KM = 3.0

# The seed route (`docs/PLAN.md` §3.2-3.3): a leg that cannot be built re-snaps a stop up to
# SNAP_FALLBACK_CANDIDATES times, after up to SEED_PENALTY_ROUNDS rounds of rebuilding the
# earlier legs with SEED_PENALTY_FACTOR × the weight on the edges of the nodes they took.
SNAP_FALLBACK_CANDIDATES = 10
SEED_PENALTY_ROUNDS = 4
SEED_PENALTY_FACTOR = 8.0
# A detour's search whose path drives an edge and one of its twins bans the later of the two and
# searches again, up to TWIN_RESEARCH_ROUNDS times (`engine/search.py`, `shortest_path`).
TWIN_RESEARCH_ROUNDS = 8
# A detour's search whose cheapest legal walk enters a node twice (a turn table can make it a loop
# round a block) bans the edge entering that node the second time and searches again, up to
# SIMPLE_RETRY_ROUNDS times (`engine/search.py`); then there is no path.
SIMPLE_RETRY_ROUNDS = 8
# A seed leg is searched by a tree over the same bans (`engine/search.py`, `leg_path`): at most
# 1 + LEG_SEARCHES searches, and one more per LEG_SEARCH_KM of the straight line between the leg's
# ends. Tuned on the national T1 build (c4a85b3d36d0, engine version 5): New York to Los Angeles
# on T0, whose cheapest legal walk enters 13 super-junctions twice and drives 9 twin pairs, needs
# 31 searches, which the chain of 8 x 8 rounds never reached; a fixed 16 refused it, and a fixed
# 64 made a refusal under a strict turn constraint on Oregon (Medford to Portland, strict
# no-right) take 52 s where the chain took 7 s and this takes 9 s. On test_search.py's small towns
# 9 searches (1 + LEG_SEARCHES) find 103 of the 107 node-simple paths the chain found 66 of, 17
# all.
LEG_SEARCHES = 8
LEG_SEARCH_KM = 100.0
# Under hard mode a seed whose legs come back to a road they left re-solves a leg with that road
# banned but for one of its stretches, up to HARD_SEED_ROUNDS times (`engine/seed_route.py`; on
# Portland's acceptance pairs 4 rounds at most were needed).
HARD_SEED_ROUNDS = 16

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
WAYPOINT_UNIFORM_PROBABILITY = 0.5  # else ∝ exp(−distance / radius)
SEARCH_BUDGET_FACTOR = 8.0
RADIUS_INITIAL_FRACTION = 0.25
RADIUS_INITIAL_MIN_M = 1000.0
ROUND_TRIP_MIN_M = 500.0
ROUND_TRIP_SEED_TRIES = 20

# The worsen loop (`docs/PLAN.md` §3.4). A window is one edge with SINGLE_EDGE_PROBABILITY, else
# a run of 1 + a geometric number of edges, RUN_EDGES_MEAN on average, at most SEGMENT_RUN_MAX.
# The radius grows RADIUS_GROWTH × on every accepted splice (up to the leash or the active
# area's extent) and halves after every RADIUS_HALVE_EVERY failures in a row
# (down to RADIUS_MIN_M); more than CONVERGED_FAILS failures in a row at the floor end a cycle,
# which starts the radius again, and CONVERGED_EMPTY_CYCLES cycles in a row that accepted
# nothing end the run.
SINGLE_EDGE_PROBABILITY = 0.8
RUN_EDGES_MEAN = 3.0
SEGMENT_RUN_MAX = 12
# Shrinking toward a target, a run is SHRINK_RUN_EDGES_MEAN edges on average, at most
# SHRINK_SEGMENT_RUN_MAX: a longer run has more of the route's wanderings to cut.
SHRINK_RUN_EDGES_MEAN = 6.0
SHRINK_SEGMENT_RUN_MAX = 24
# With SEGMENT_BIAS_PROBABILITY a window starts at one of the SEGMENT_BIAS_TOP route edges with
# the most unused active nodes within WAYPOINT_BALL_FACTOR × the radius of their midpoint, counted
# on a grid of SEGMENT_BIAS_CELL_M cells (a square window approximates the ball), or of the
# larger cells that keep the grid within UNUSED_GRID_MAX_CELLS over a wide active area.
SEGMENT_BIAS_PROBABILITY = 0.3
SEGMENT_BIAS_TOP = 5
SEGMENT_BIAS_CELL_M = 100.0
UNUSED_GRID_MAX_CELLS = 2_000_000
RADIUS_GROWTH = 1.5
RADIUS_HALVE_EVERY = 20
RADIUS_MIN_M = 300.0
CONVERGED_FAILS = 400
CONVERGED_EMPTY_CYCLES = 3
# Targets (`engine/target.py`): a ratio or duration target is reached within ±TARGET_TOLERANCE
# of its value; a duration target is at most TARGET_MAX_DURATION_S.
TARGET_TOLERANCE = 0.02
TARGET_MAX_DURATION_S = 7 * 24 * 3600.0
# Turns (`engine/turns.py`): by the change of bearing from one edge into the next, wrapped to
# (−180, 180] degrees clockwise, a turn is straight within ±TURN_STRAIGHT_DEG, a right or a left
# turn up to ±TURN_UTURN_DEG, and a U-turn beyond.
TURN_STRAIGHT_DEG = 30
TURN_UTURN_DEG = 160
# A soft turn constraint (`engine/constraints.py`) scores each turn it is about at minus a
# penalty in its objective's score units: TURN_PENALTY_M metres when the score is a length (one
# banned turn costs a kilometre; 10⁶ makes it lexicographic, which on Portland gives 1.1×
# routes), TURN_PENALTY_S seconds under the slowest time (about a kilometre at 40 km/h; on
# Portland, Powell's to Reed soft no-left under the slowest time, seed 1: 28.48x the experienced
# time with 1.09 left turns a km at 90 s, 11.66x with 0.85 at 180 s, 4.67x with 0.54 at 300 s,
# so none keeps both under 0.7 a km and well above 10x, and it stays 90 s).
TURN_PENALTY_M = 1000.0
TURN_PENALTY_S = 90.0
# Collecting (`engine/objectives/collect.py`): the score is items × COLLECT_ITEM_WEIGHT + the
# length in metres, items first and length second as long as a splice changes the length by
# less than the weight (1,000 km). The first radius is COLLECT_RADIUS_FRACTION of the baseline,
# at least COLLECT_RADIUS_MIN_M: items are sparse, so detours start wider than distance's.
COLLECT_ITEM_WEIGHT = 1e6
COLLECT_RADIUS_FRACTION = 0.5
COLLECT_RADIUS_MIN_M = 2000.0
# The alphabet route (`engine/alphabet_search.py`; `docs/PLAN.md` §3.8): the score is letters ×
# ALPHABET_LETTER_WEIGHT + the length (less a soft turn constraint's penalties), letters first.
# The roads of the next letter are looked for within ALPHABET_RADIUS_M of driving, then twice
# that while it is under the leash, then anywhere in the request's area; a road's lookahead counts
# the roads of the letter after it within ALPHABET_RADIUS_M (straight line) of where its ride
# ends. A step tries at most ALPHABET_CANDIDATE_TRIES roads, and a run ends "converged" after
# ALPHABET_PATIENCE restarts in a row that found no better route. Tuned on Portland (build
# ffe2e7c881ca), Powell's to Reed and to PDX, seeds 1-3, 30 s: a better route still turns up
# after hundreds of restarts, so 100 gave 9-20 letters, 300 gave 10-23 and 1000 gave 13-23
# (20-23 in five runs of six), each converging in 10-27 s or running to the budget.
ALPHABET_LETTER_WEIGHT = 1e6
ALPHABET_RADIUS_M = 1500.0
ALPHABET_CANDIDATE_TRIES = 8
ALPHABET_PATIENCE = 1000

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
# Stage B (`pipeline/consolidate.py`): ramps (`*_link`) and roundabouts become super-junctions. Ramp
# components whose attachment roads share two identities and lie within STAGE_B_JOIN_M of each other
# are one interchange, and so are two clusters with attachment nodes within STAGE_B_JOIN_M on one
# carriageway (one highway of STAGE_B_CARRIAGEWAY_CLASSES with one ref set, else one name) that
# leaves one, passes the other and comes back within STAGE_B_MAX_DIAMETER_M (I-5 northbound at
# Woodburn, whose halves share only I-5); a cluster whose links are all minor (STAGE_B_MINOR_LINKS)
# takes in the at-grade node its slip lanes bypass, a neighbour of two of its attachment nodes
# through two different roads within STAGE_B_SLIP_CENTER_M. A cluster then takes in the nodes of a
# pocket: a path of at most STAGE_B_POCKET_M along one road (one ref set, or one name) leaving the
# cluster and coming back to it (a bridge between ramp terminals). A cluster wider than
# STAGE_B_MAX_DIAMETER_M (bbox diagonal) is split by single linkage of its attachment nodes at
# STAGE_B_SPLIT_EPS_M. Roundabouts never split.
STAGE_B_JOIN_M = 500.0
STAGE_B_CARRIAGEWAY_CLASSES = frozenset({"motorway", "trunk", "primary"})
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
# component keeps fewer than SCC_MIN_RETAINED of the nodes, or when a component it drops holds
# more than SCC_MAX_DROPPED_NODES (a town whose only way in a filtering rule removed), unless
# --allow-fragmented. A state holds many small road networks reached only by tracks or private
# roads, and the roads its extract's edge cuts off: Oregon keeps 98.49% of its nodes, dropping
# 1,255 components of at most 88 nodes; Portland keeps 99.60%, dropping at most 11 at once.
SCC_MIN_RETAINED = 0.98
SCC_MAX_DROPPED_NODES = 200

# Enrich (`docs/PLAN.md` §2.5; `docs/reference/pipeline.md`, enrich). An edge's speed is its
# `maxspeed` tag when it parses, else its class's default here, in mph; a node's delay is the
# largest NODE_DELAY_S of the stop signs and signals merged into it; an edge's bearings are taken
# over the first and the last BEARING_SAMPLE_M of its geometry. A brand POI is snapped to the
# nearest node a route can pass through within BRAND_SNAP_M; a brand on fewer than
# BRAND_MIN_LOCATIONS distinct nodes is left out of the build's brand index.
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
BRAND_SNAP_M = 100.0
BRAND_MIN_LOCATIONS = 3

# Save (`docs/reference/pipeline.md`, save): each edge's shape is simplified to this tolerance
# (Douglas–Peucker, its ends kept) before the build's geometry stores it (`graph/geometry.py`).
# Lengths and bearings are measured on the whole shape at enrich, before.
GEOMETRY_SIMPLIFY_M = 2.0

# Tiers (`docs/PLAN.md` §2.6; `pipeline/tiers.py`): each tier keeps the largest strongly
# connected component of its own roads; the step fails when a tier keeps fewer than this
# fraction of the nodes its roads touch, unless --allow-fragmented.
TIER_MIN_RETAINED = 0.95

# QA (`docs/PLAN.md` §2.8; `pipeline/qa.py`): a build is accepted when twins cover at least this
# share of one-way motorway and trunk length, cleanup kept SCC_MIN_RETAINED of the nodes, the
# largest component it dropped held at most SCC_MAX_DROPPED_NODES ("SCC largest dropped"), and
# every tier TIER_MIN_RETAINED of its own.
QA_TWIN_COVERAGE_MIN = 0.95

# The API (`docs/reference/api.md`). At most API_MAX_JOBS jobs run at once, each in a worker
# process, and API_MAX_QUEUED wait; a running job is killed API_KILL_GRACE_S after its budget. A
# job's record is kept API_JOB_TTL_S after it ends. A job whose SSE subscribers have all gone
# for API_ORPHAN_S (once one had connected) is cancelled, or dropped if still queued; the
# manager looks for those every API_SWEEP_S. A job's follower waits at most API_READ_GRACE_S on
# its worker's pipe before it looks at the job's time limit and its worker again (a worker that
# dies, even partway through a message, closes the pipe, which ends the wait at once). Progress
# is sent at most once per API_COALESCE_S, with a full snapshot every API_SNAPSHOT_EVERY
# progress events, simplified to API_SIMPLIFY_M. The API's default max_iterations leaves the
# seconds budget to end a run (the CLI keeps DEFAULT_MAX_ITERATIONS).
API_MAX_JOBS = max(1, (os.cpu_count() or 1) - 2)
API_MAX_QUEUED = 8
API_KILL_GRACE_S = 30.0
API_JOB_TTL_S = 3600.0
API_ORPHAN_S = 15.0
API_SWEEP_S = 1.0
API_READ_GRACE_S = 0.1
API_COALESCE_S = 0.1
API_SNAPSHOT_EVERY = 50
API_SIMPLIFY_M = 1.0
API_DEFAULT_MAX_ITERATIONS = 1_000_000

# The daily route (`docs/reference/api.md`, **The daily route**): two incorporated places
# (DAILY_LSAD: a city, a town or a village) of the server's main build's places index, each
# inside the build's bbox with a node within DAILY_NEAR_KM that can play its stop's role, whose
# straight-line distance lies within DAILY_BAND of the build's bbox diagonal (fractions; at most
# DAILY_DRAWS pairs are drawn), the first such pair the request's pre-flight accepts (at most
# DAILY_TRIES tried); planned as bad as possible with DAILY_MAX_ITERATIONS attempts, so that the
# iterations, not the clock, end the run and every server with the same build gets the same route,
# with the frontend's default vehicle (DAILY_MPG, DAILY_GAS_PRICE_USD) for its fuel numbers.
DAILY_LSAD = ("25", "43", "47")
DAILY_NEAR_KM = 5.0
DAILY_BAND = (0.15, 0.45)
DAILY_DRAWS = 10_000
DAILY_TRIES = 10
DAILY_MAX_ITERATIONS = 5_000
DAILY_MPG = 24.0
DAILY_GAS_PRICE_USD = 3.89


# Geocoding (`docs/reference/api.md`, **Geocoding**; `docs/PLAN.md` §4.4). The proxy sends its
# requests upstream one at a time, each at least 1 / GEOCODE_RPS seconds after the previous one's
# answer came back, from the whole process, as Nominatim's usage policy asks; a search is refused
# (RATE_LIMITED) when the queue it would join holds GEOCODE_MAX_WAIT_S of intervals already.
# After Nominatim refuses (429 or 403) the proxy asks nothing for GEOCODE_PAUSE_S. Answers are
# cached GEOCODE_CACHE_TTL_S, at most GEOCODE_LIMIT candidates each; an upstream request times
# out after GEOCODE_TIMEOUT_S; a query is at most GEOCODE_MAX_QUERY_CHARS once normalised.
# GeocoderName is the API's choice of geocoder ("none" turns the endpoint off), NOMINATIM_URL
# the public instance's address.
GeocoderName = Literal["nominatim", "none"]
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
GEOCODE_RPS = 1.0
GEOCODE_MAX_WAIT_S = 5.0
GEOCODE_PAUSE_S = 60.0
GEOCODE_CACHE_TTL_S = 30 * 86400.0
GEOCODE_LIMIT = 5
GEOCODE_TIMEOUT_S = 10.0
GEOCODE_MAX_QUERY_CHARS = 200

# Public hosting (`docs/reference/api.md`, **Public limits**; `docs/reference/hosting.md`), only
# with `serve --public`: a client (`api/limits.py`) runs at most PUBLIC_RUNNING_PER_CLIENT jobs at
# once and has at most PUBLIC_QUEUED_PER_CLIENT more waiting, a budget is at most
# PUBLIC_BUDGET_MAX_S (BUDGET_MAX_S otherwise), a client searches places at most
# PUBLIC_GEOCODE_PER_MINUTE times in any minute and asks which build its stops run on
# (`POST /regions/pick`, once a change of its stops) at most PUBLIC_PICK_PER_MINUTE times. A
# request's own leash stays at most LEASH_MAX_KM;
# the automatic leash follows the trip (LEASH_LONG_FACTOR of it beyond 750 km), and the budget
# bounds the job.
PUBLIC_RUNNING_PER_CLIENT = 1
PUBLIC_QUEUED_PER_CLIENT = 3
PUBLIC_BUDGET_MAX_S = 60.0
PUBLIC_GEOCODE_PER_MINUTE = 10
PUBLIC_PICK_PER_MINUTE = 60


# Census boundaries (`docs/reference/presentation.md`, **Places**). CENSUS_VINTAGE is the year of
# the cartographic boundary files `tools/fetch_census.sh` fetches and `worst-route aux places`
# reads; STATES maps each state's Geofabrik slug (a Region's `state`) to its USPS code and FIPS
# code, for the 50 states and DC.
CENSUS_VINTAGE = 2025
STATES: dict[str, tuple[str, str]] = {
    "alabama": ("AL", "01"),
    "alaska": ("AK", "02"),
    "arizona": ("AZ", "04"),
    "arkansas": ("AR", "05"),
    "california": ("CA", "06"),
    "colorado": ("CO", "08"),
    "connecticut": ("CT", "09"),
    "delaware": ("DE", "10"),
    "district-of-columbia": ("DC", "11"),
    "florida": ("FL", "12"),
    "georgia": ("GA", "13"),
    "hawaii": ("HI", "15"),
    "idaho": ("ID", "16"),
    "illinois": ("IL", "17"),
    "indiana": ("IN", "18"),
    "iowa": ("IA", "19"),
    "kansas": ("KS", "20"),
    "kentucky": ("KY", "21"),
    "louisiana": ("LA", "22"),
    "maine": ("ME", "23"),
    "maryland": ("MD", "24"),
    "massachusetts": ("MA", "25"),
    "michigan": ("MI", "26"),
    "minnesota": ("MN", "27"),
    "mississippi": ("MS", "28"),
    "missouri": ("MO", "29"),
    "montana": ("MT", "30"),
    "nebraska": ("NE", "31"),
    "nevada": ("NV", "32"),
    "new-hampshire": ("NH", "33"),
    "new-jersey": ("NJ", "34"),
    "new-mexico": ("NM", "35"),
    "new-york": ("NY", "36"),
    "north-carolina": ("NC", "37"),
    "north-dakota": ("ND", "38"),
    "ohio": ("OH", "39"),
    "oklahoma": ("OK", "40"),
    "oregon": ("OR", "41"),
    "pennsylvania": ("PA", "42"),
    "rhode-island": ("RI", "44"),
    "south-carolina": ("SC", "45"),
    "south-dakota": ("SD", "46"),
    "tennessee": ("TN", "47"),
    "texas": ("TX", "48"),
    "utah": ("UT", "49"),
    "vermont": ("VT", "50"),
    "virginia": ("VA", "51"),
    "washington": ("WA", "53"),
    "west-virginia": ("WV", "54"),
    "wisconsin": ("WI", "55"),
    "wyoming": ("WY", "56"),
}

# Regions of several states (M5.3; `docs/reference/pipeline.md`, **Regions of several states**):
# their extracts are read in this order and merged at `filter`. `us-t1` is the 48 contiguous
# states and DC at T1, the national build that validated the pipeline; `us-t3` the same states at
# T3, the national build the server serves (the owner, 2026-10-31); `us` is the same states at
# every road class, the all-roads master graph M5.3's decision weighed, which this machine cannot
# build as the steps stand; `west-coast` (California,
# Oregon and Washington, about eight times Oregon) is the intermediate scale between a state and
# the country that its measurements are taken at. Alaska and Hawaii have no road to them and lie
# outside CONUS Albers (EPSG:5070), so they would be regions of their own.
_CONUS = tuple(state for state in STATES if state not in ("alaska", "hawaii"))
REGIONS["oregon-washington"] = Region(
    name="oregon-washington",
    display="Oregon and Washington",
    states=("oregon", "washington"),
    crs_epsg=32610,
)
REGIONS["oregon-washington-t1"] = Region(
    name="oregon-washington-t1",
    display="Oregon and Washington",
    states=("oregon", "washington"),
    crs_epsg=32610,
    classes="T1",
)
REGIONS["us-t1"] = Region(
    name="us-t1", display="United States", states=_CONUS, crs_epsg=5070, classes="T1"
)
REGIONS["us-t3"] = Region(
    name="us-t3", display="United States", states=_CONUS, crs_epsg=5070, classes="T3"
)
REGIONS["us"] = Region(name="us", display="United States", states=_CONUS, crs_epsg=5070)
REGIONS["west-coast"] = Region(
    name="west-coast",
    display="West Coast",
    states=("california", "oregon", "washington"),
    crs_epsg=5070,
)
# One all-roads region for each contiguous state and DC, named by its slug: regions one may build
# (`docs/reference/pipeline.md`, **The state builds**, where they measure the array pipeline at
# state scale), projected to CONUS Albers as the national builds are, for one server to mix them.
# Oregon keeps its own region, in its UTM zone, and its build.
REGIONS.update({s: Region(name=s, states=(s,), crs_epsg=5070) for s in _CONUS if s not in REGIONS})

# Presentation (`docs/reference/presentation.md`). Directions: a left or right turn is "slight"
# when its change of bearing is at most DIRECTIONS_SLIGHT_DEG and "sharp" beyond
# DIRECTIONS_SHARP_DEG (the turn classes themselves are TURN_STRAIGHT_DEG and TURN_UTURN_DEG's).
# Jokes come after about every JOKE_EVERY-th driving step (more apart on a route with more such
# steps than jokes), each gap drawn within ±JOKE_JITTER of the mean. A route has at most
# TOASTS_MAX toasts, as many as it can have at once, so none is ever dropped.
DIRECTIONS_SLIGHT_DEG = 45
DIRECTIONS_SHARP_DEG = 120
JOKE_EVERY = 8
JOKE_JITTER = 0.25
TOASTS_MAX = 12
