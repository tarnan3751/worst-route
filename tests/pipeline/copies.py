"""Mill Rd mapped more than once, as OSM files for the tests of the pipeline and the engine: copies
of the road over one run of OSM nodes (`mapped_copies`), and the build meta `graph_data` needs to
turn such a graph into `GraphData` (`META`).
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
