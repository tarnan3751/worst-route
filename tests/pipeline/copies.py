"""Roads mapped more than once, as OSM files for the tests of the pipeline and the engine: copies
of Mill Rd over one run of OSM nodes (`mapped_copies`), two one-way ways of Mill Rd over one node
in opposite directions, over the same nodes or overlapping in part (`overlapping`), US 322's two
carriageways meeting at one node (`us_322`), and the build meta `graph_data` needs to turn such
a graph into `GraphData` (`META`).
"""

from pathlib import Path
from typing import Any

from tests.pipeline.osm import write_osm
from worst_route.config import PIPELINE_VERSION
from worst_route.graph.data import FORMAT_VERSION

META: dict[str, Any] = {
    "region": "test",
    "build_id": "b1",
    "crs_epsg": 32610,
    "created": "2026-10-30T00:00:00+00:00",
    "pipeline_version": PIPELINE_VERSION,
    "format": FORMAT_VERSION,
    "params": {"bbox": None},
    "sources": [],
}

MILL_RD = {"highway": "secondary", "name": "Mill Rd"}
# Copies of Mill Rd over one run of OSM nodes, each copy (drawn against the run, one-way).
COPIES = {
    "three two-way copies": [(False, False), (True, False), (False, False)],
    "two one-way copies and a two-way copy": [(False, True), (True, True), (False, False)],
    "a one-way copy and a two-way copy": [(False, True), (False, False)],
}


def mapped_copies(path: Path, copies: list[tuple[bool, bool]], *, inner: bool) -> Path:
    """Mill Rd mapped several times over the OSM nodes 1, 3 (1, 2, 3 with `inner`), each copy a
    way of its own drawn along the run or against it, one-way or two-way, in a block with Oak
    St (4-1), Elm St (3-5) and Back Rd (4-5)."""
    run = [1, 2, 3] if inner else [1, 3]
    ways = [
        (10 + k, run[::-1] if against else run, {**MILL_RD, "oneway": "yes"} if oneway else MILL_RD)
        for k, (against, oneway) in enumerate(copies)
    ]
    ways += [
        (1, [4, 1], {"highway": "residential", "name": "Oak St"}),
        (2, [3, 5], {"highway": "residential", "name": "Elm St"}),
        (3, [4, 5], {"highway": "residential", "name": "Back Rd"}),
    ]
    nodes = {1: (0, 0), 2: (150, 0), 3: (300, 0), 4: (0, -200), 5: (300, 200)}
    return write_osm(path, nodes, ways)


# Mill Rd as two one-way ways over node 2 in opposite directions, each (way id, OSM nodes) in the
# order the file lists them, and the runs of OSM nodes simplification leaves along Mill Rd.
OVERLAPS = {
    "one road": ([(1, [1, 2, 3]), (2, [3, 2, 1])], [(1, 2, 3), (3, 2, 1)]),
    "overlapping in part": (
        [(1, [1, 2, 3]), (2, [3, 2, 6, 1])],
        [(1, 2), (2, 3), (2, 6, 1), (3, 2)],
    ),
    "overlapping in part, the second way listed first": (
        [(2, [3, 2, 6, 1]), (1, [1, 2, 3])],
        [(1, 2), (2, 3), (2, 6, 1), (3, 2)],
    ),
    "a shape point on each side": (
        [(1, [1, 7, 2, 3]), (2, [3, 2, 6, 1])],
        [(1, 7, 2), (2, 3), (2, 6, 1), (3, 2)],
    ),
}


def overlapping(path: Path, mill_rd: list[tuple[int, list[int]]]) -> Path:
    """Mill Rd as one-way ways, each (way id, OSM nodes), listed in that order, between Oak St
    (4-1) and Elm St (3-5): east from 1 over node 2 to 3, perhaps with node 7 of its own between
    1 and 2, and back from 3 over node 2 to 1, over the same nodes or with node 6 of its own."""
    one_way = {**MILL_RD, "oneway": "yes"}
    nodes = {1: (0, 0), 2: (150, 0), 3: (300, 0), 4: (0, -200), 5: (300, 200)}
    nodes |= {6: (75, 0.5), 7: (75, -20)}
    ways = [(way, refs, one_way) for way, refs in mill_rd]
    ways += [
        (3, [4, 1], {"highway": "residential", "name": "Oak St"}),
        (4, [3, 5], {"highway": "residential", "name": "Elm St"}),
    ]
    return write_osm(path, nodes, ways)


US_322_NODE = 106661013


def us_322(path: Path) -> Path:
    """US 322, the Philipsburg Bigler Highway in Clearfield County, PA, where the national build's
    save refused node 106661013 as lying inside two roads: four one-way trunk ways of the road
    form two loops through it, a divided stretch east of it (425447408 out along the south side
    to 106661019, 425447409 back along the north side) and one west of it (1534168462 out along
    the north side to 106453508, 1534168463 back along the south side), so it has two edges in and
    two out and no in-edge runs back over an out-edge's nodes. The way and node ids are the
    extract's; inner nodes the extract did not name have ids of their own (9001-9007, 9011-9014);
    the positions are metres."""
    road = {"highway": "trunk", "ref": "US 322", "name": "Philipsburg Bigler Highway"}
    road |= {"oneway": "yes"}
    nodes = {US_322_NODE: (0, 0), 106661015: (130, -15), 106661017: (270, -15)}
    nodes |= {106661019: (400, 0), 106625033: (300, 15), 4247716658: (200, 15)}
    nodes |= {4247716652: (100, 15), 106453508: (-800, 0)}
    north = {9001 + k: (-100.0 * (k + 1), 15) for k in range(7)}
    south = [106661006, 13979653578, 9011, 9012, 9013, 9014, 106661010]
    nodes |= north | {node: (-100.0 * (7 - k), -15) for k, node in enumerate(south)}
    ways = [
        (425447408, [US_322_NODE, 106661015, 106661017, 106661019], road),
        (425447409, [106661019, 106625033, 4247716658, 4247716652, US_322_NODE], road),
        (1534168462, [US_322_NODE, *north, 106453508], road),
        (1534168463, [106453508, *south, US_322_NODE], road),
    ]
    return write_osm(path, nodes, ways)
