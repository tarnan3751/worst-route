"""Golden routes: a fixed build, request and seed give the same route, byte for byte.

The build is a 20 × 20 lattice of 100 m streets whose lon/lat are rounded to 1e-7 degrees, so its
bytes, and its pinned graph_version, do not depend on PROJ's last bits on the machine running
the test. Every backend must reproduce it, byte for byte. A golden file changes only under a
tier A brief that says so; to rewrite it, run `WORST_ROUTE_REGEN_GOLDEN=1 uv run pytest
tests/golden` and commit the file.
"""

import json
import os
from pathlib import Path

import numpy as np
import pytest

from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.python_backend import PythonBackend

from ..engine.graphs import at_node, lattice

GOLDEN = Path(__file__).with_name("grid20_distance.json")


@pytest.mark.parametrize("backend_type", [PythonBackend, CsrBackend], ids=["python", "csr"])
def test_grid_route_is_golden(backend_type: type[PythonBackend] | type[CsrBackend]) -> None:
    g = lattice(20, 20, rounded_lonlat=True)
    stops = [at_node(g, v) for v in (0, 210, 399)]
    request = RouteRequest(stops=stops, seed=7, max_iterations=400, budget_s=120)
    result = plan_route(g, request, backend=backend_type(g), check_delta=True)
    route = result.route
    actual = {
        "graph_version": g.meta["graph_version"],
        "request": request.model_dump(mode="json"),
        "nodes": route.nodes.tolist(),
        "edges": route.edges.tolist(),
        "stop_idx": list(route.stop_idx),
        "length_m": route.metrics.length_m,
        "attempts": result.run.attempts,
        "accepted": result.run.accepted,
    }
    if os.environ.get("WORST_ROUTE_REGEN_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(actual, indent=1) + "\n")
        pytest.skip(f"rewrote {GOLDEN.name}")

    golden = json.loads(GOLDEN.read_text())
    assert golden["graph_version"] == actual["graph_version"]
    assert golden["request"] == actual["request"]
    assert np.array(golden["nodes"], dtype=np.int32).tobytes() == route.nodes.tobytes()
    assert np.array(golden["edges"], dtype=np.int32).tobytes() == route.edges.tobytes()
    assert golden == actual
