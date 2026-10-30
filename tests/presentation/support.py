"""The places fixture (`tests/fixtures/census/`) and a route over it, shared by the presentation
and API tests.

The fixture holds two fake states split at lon −122.674, through the middle of the 10 × 10
lattice the API tests serve (lon −122.680 to −122.668, lat 45.513 to 45.521): Westland (91, WL)
to the west, Eastland (92, EL) to the east; and one places file per state, as the Census
publishes them: Westville (city) at the lattice's south-west, Faraway (village) well north-west
of it, Middlefield (a CDP) just east of the border and Eastbrook (town) at its north-east.
`EAST_THEN_NORTH` runs along row 1 from its west end, then up column 9: Westville, the border,
Middlefield, then Eastbrook.
"""

from pathlib import Path

from worst_route.presentation.places import PlacesWritten, places_dir, write_places

from ..engine.graphs import edges_of, lattice

CENSUS = Path(__file__).parents[1] / "fixtures" / "census"
STATES_FILE = CENSUS / "states.geojson"
PLACES = {"91": CENSUS / "places_91.geojson", "92": CENSUS / "places_92.geojson"}
WIDE = (-122.8, 45.4, -122.5, 45.6)  # every fixture place

GRID = lattice(10, 10, rounded_lonlat=True)
EAST_THEN_NORTH = [*range(10, 20), *range(29, 100, 10)]
EAST_THEN_NORTH_EDGES = edges_of(GRID, EAST_THEN_NORTH)


def write_fixture_places(
    data_dir: Path,
    bbox: tuple[float, float, float, float] | None = WIDE,
    states: tuple[str, ...] = ("91", "92"),
    region: str = "portland",
) -> PlacesWritten:
    """The places index from the fixture, under `data_dir`, with the places files of `states`
    (the fixture's states file stands for both the 5m and the 20m one)."""
    return write_places(
        region,
        bbox,
        states=STATES_FILE,
        outlines=STATES_FILE,
        places=[PLACES[s] for s in states],
        out_dir=places_dir(data_dir),
    )
