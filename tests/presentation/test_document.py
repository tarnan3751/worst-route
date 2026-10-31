"""`present` and `with_presentation`: what a document adds, the same from a planned route and
from its route.json."""

import json
from pathlib import Path

from worst_route.engine.io import read_route, route_document
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.presentation.document import present, with_presentation

from ..engine.graphs import at_node, lattice

GRID = lattice(8, 8, rounded_lonlat=True)


def test_a_planned_route_and_its_file_present_the_same(tmp_path: Path) -> None:
    request = RouteRequest(
        stops=[at_node(GRID, v) for v in (0, 27, 63)], seed=2, max_iterations=150
    )
    result = plan_route(GRID, request)
    route, masks = result.route, result.masks
    document = route_document(GRID, result)
    fields = {"active": masks.active, "banned": masks.banned, "seed": 2, "vehicle": None,
              "places": None, "labels": ["a", None, "c"]}  # fmt: skip
    presented = present(
        GRID, route.nodes, route.edges, route.stop_idx, document["metrics"], **fields
    )
    assert set(presented) == {"directions", "toasts", "metrics_extra"}
    assert presented["directions"][-1]["text"] == "Arrive at c"

    written = with_presentation(document, presented)
    assert list(written) == [*document, "directions", "toasts"]
    assert written["metrics"] == {**document["metrics"], **presented["metrics_extra"]}
    path = tmp_path / "route.json"
    path.write_text(json.dumps(written))
    saved = read_route(path)
    again = present(
        GRID, saved.nodes, saved.edges, saved.stop_idx, saved.document["metrics"], **fields
    )
    assert again == presented
