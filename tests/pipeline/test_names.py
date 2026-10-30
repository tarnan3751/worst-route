import pytest

from worst_route.pipeline.names import (
    name_without_directions,
    normalize_name,
    normalize_ref,
    road_signature,
)


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("I-5", ("I 5",)),
        ("I 5", ("I 5",)),
        ("I5", ("I 5",)),
        ("Interstate 5", ("I 5",)),
        ("interstate-205", ("I 205",)),
        ("US 26", ("US 26",)),
        ("US Highway 26", ("US 26",)),
        ("US Hwy 26", ("US 26",)),
        ("U.S. 26", ("US 26",)),
        ("US26", ("US 26",)),
        ("OR 99W", ("OR 99W",)),
        ("OR-99W", ("OR 99W",)),
        ("or  99w", ("OR 99W",)),
        ("WA 14", ("WA 14",)),
        ("State Route 14", ("SR 14",)),
        ("State Highway 8", ("SR 8",)),
        ("SR 500", ("SR 500",)),
        ("US 26;OR 8", ("US 26", "OR 8")),
        ("I 84; US 30", ("I 84", "US 30")),
        ("US 26;US 26", ("US 26",)),
        ("US 30 Business", ("US 30", "BUSINESS")),
        ("US 30 Bus", ("US 30", "BUSINESS")),
        ("I-205 Alt", ("I 205", "ALT")),
        ("I-205 Alternate", ("I 205", "ALT")),
        ("OR 213 Spur", ("OR 213", "SPUR")),
        ("I 5;I 5 Business", ("I 5", "BUSINESS")),
        ("", ()),
        (" ; ", ()),
    ],
)
def test_normalize_ref(ref: str, expected: tuple[str, ...]) -> None:
    assert normalize_ref(ref) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("SE Powell Blvd", "southeast powell boulevard"),
        ("Southeast Powell Boulevard", "southeast powell boulevard"),
        ("N Lombard St.", "north lombard street"),
        ("NE Martin Luther King Jr Blvd", "northeast martin luther king jr boulevard"),
        ("SW Barbur  Blvd", "southwest barbur boulevard"),
        ("Main St N", "main street north"),
        ("St Johns Bridge", "st johns bridge"),  # a leading St is a saint, not a street
        ("NW Cornell Rd", "northwest cornell road"),
        ("SE Foster Rd", "southeast foster road"),
        ("Sunset Hwy", "sunset highway"),
        ("Murray Pkwy", "murray parkway"),
        ("Oak Ave", "oak avenue"),
        ("Elm Dr", "elm drive"),
        ("Fir Ln", "fir lane"),
        ("Ash Ct", "ash court"),
        ("Pine Pl", "pine place"),
        ("Hill Ter", "hill terrace"),
        ("Lake Cir", "lake circle"),
        ("River Wy", "river way"),
        ("E Burnside", "east burnside"),
        ("W Burnside", "west burnside"),
        ("S Macadam Ave", "south macadam avenue"),
        ("NW 23rd Ave", "northwest 23rd avenue"),
    ],
)
def test_normalize_name(name: str, expected: str) -> None:
    assert normalize_name(name) == expected


def test_road_signature() -> None:
    assert road_signature({"ref": "OR-99W", "name": "SW Barbur Blvd"}) == frozenset({"OR 99W"})
    assert road_signature({"ref": "US 26;OR 8"}) == road_signature({"ref": "OR 8; US-26"})
    assert road_signature({"name": "SE Powell Blvd"}) == "southeast powell boulevard"
    assert road_signature({"ref": " ", "name": "Main St"}) == "main street"  # an empty ref
    assert road_signature({"highway": "motorway"}) is None
    # A ref and a name never compare equal, even when they spell the same thing.
    assert road_signature({"ref": "I 5"}) != road_signature({"name": "I 5"})


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Pacific Highway East", "pacific highway"),
        ("Southwest Pacific Highway East", "pacific highway"),
        ("SW Pacific Hwy E", "pacific highway"),
        ("N Lombard St", "lombard street"),
        ("East Burnside St", "burnside street"),
        ("North", ""),
    ],
)
def test_name_without_directions(name: str, expected: str) -> None:
    assert name_without_directions(name) == expected
