from pathlib import Path

import osmium
import pytest

from worst_route.config import KEPT_HIGHWAY
from worst_route.pipeline.exclusions import is_kept, kept_class

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"

DROPPED_HIGHWAY = [
    "footway",
    "path",
    "track",
    "cycleway",
    "steps",
    "pedestrian",
    "bridleway",
    "corridor",
    "busway",
    "bus_guideway",
    "raceway",
    "escape",
    "construction",
    "proposed",
    "abandoned",
    "platform",
    "rest_area",
    "services",
    "road",
    "service",
]


@pytest.mark.parametrize("highway", sorted(KEPT_HIGHWAY))
def test_kept_classes(highway: str) -> None:
    assert kept_class({"highway": highway}) == highway


def test_kept_classes_are_the_plans() -> None:
    assert {
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
    } == KEPT_HIGHWAY


@pytest.mark.parametrize("highway", DROPPED_HIGHWAY)
def test_dropped_classes(highway: str) -> None:
    assert kept_class({"highway": highway}) is None


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({}, None),
        ({"building": "yes"}, None),
        ({"highway": "service", "service": "alley"}, "alley"),
        ({"highway": "service", "service": "driveway"}, None),
        ({"highway": "service", "service": "parking_aisle"}, None),
        ({"highway": "service", "service": "drive-through"}, None),
        ({"highway": "service", "service": "emergency_access"}, None),
        ({"highway": "residential", "service": "alley"}, "residential"),
        ({"highway": "residential", "area": "yes"}, None),
        ({"highway": "service", "service": "alley", "area": "yes"}, None),
        ({"highway": "residential", "area": "no"}, "residential"),
        ({"highway": "residential", "access": "private"}, None),
        ({"highway": "residential", "access": "no"}, None),
        ({"highway": "residential", "access": "private", "motor_vehicle": "yes"}, "residential"),
        ({"highway": "residential", "access": "no", "motorcar": "yes"}, "residential"),
        ({"highway": "residential", "access": "private", "motor_vehicle": "private"}, None),
        ({"highway": "residential", "access": "destination"}, "residential"),
        ({"highway": "residential", "access": "permissive"}, "residential"),
        ({"highway": "residential", "access": "yes"}, "residential"),
        ({"highway": "residential", "motor_vehicle": "no"}, None),
        ({"highway": "residential", "motor_vehicle": "private"}, None),
        ({"highway": "residential", "motorcar": "no"}, None),
        ({"highway": "residential", "motorcar": "private"}, None),
        # The most specific key present decides: motorcar, then motor_vehicle, then access.
        ({"highway": "residential", "access": "yes", "motor_vehicle": "no"}, None),
        ({"highway": "residential", "motor_vehicle": "no", "motorcar": "yes"}, "residential"),
        ({"highway": "residential", "motor_vehicle": "yes", "motorcar": "no"}, None),
        ({"highway": "residential", "access": "no", "motor_vehicle": "destination"}, "residential"),
        ({"highway": "trunk", "access": "no", "motor_vehicle": "no", "motorcar": "yes"}, "trunk"),
        ({"highway": "residential", "access": "yes", "motorcar": "private"}, None),
        ({"highway": "service", "service": "alley", "access": "private"}, None),
        ({"highway": "motorway", "access": "no", "motor_vehicle": "yes"}, "motorway"),
    ],
)
def test_rules(tags: dict[str, str], expected: str | None) -> None:
    assert kept_class(tags) == expected
    assert is_kept(tags) is (expected is not None)


def test_reads_pyosmium_tags_in_place() -> None:
    kept = {
        way.id: kept_class(way.tags)
        for way in osmium.FileProcessor(FIXTURES / "exclusions.osm", osmium.osm.WAY)
    }
    assert {way_id: c for way_id, c in kept.items() if c is not None} == {
        200: "residential",
        221: "alley",
        222: "residential",
        223: "living_street",
        224: "unclassified",
    }
    assert sorted(way_id for way_id, c in kept.items() if c is None) == list(range(201, 212))
