"""Golden routes: a fixed build, request and seed give the same route, byte for byte.

The build is a 20 × 20 lattice of 100 m streets, its node positions whole metres, so its bytes,
and its pinned graph_version, do not depend on PROJ's last bits on the machine running the test;
its stops are its nodes' lon/lat rounded to 1e-7 degrees (`at_node`), for the same reason.
Every backend must reproduce it, byte for byte. Two more freeze target runs from the
same stops and seed (a ratio and a duration target, each stopping within it), and two more the
modes, with the same seed: the slowest time from the same stops on the lattice with a primary
road every fourth row and column (signals where two cross, stop signs along them), and the
distance with a soft no-left constraint between inner nodes; one a run collecting bridges on
the lattice with a few roads made bridges; one a pilgrimage to the second of two brands placed
by hand on the lattice; one the distance under hard mode, on the lattice with its rows and
columns named; and one an alphabet route on the lattice with its rows named after trees in a
fixed shuffled order and its columns numbered avenues but two named ones. A golden file
changes only under a tier A brief that says so; to rewrite one, run
`WORST_ROUTE_REGEN_GOLDEN=1 uv run pytest tests/golden` and commit the file.
"""

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from worst_route.engine.plan import PlanResult, plan_route
from worst_route.engine.request import RouteRequest
from worst_route.engine.target import Target
from worst_route.engine.turns import count_turns
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import EdgeFlag, GraphData
from worst_route.graph.python_backend import PythonBackend

from ..engine.graphs import TREES, at_node, avenue, lattice, named_roads

GOLDEN = Path(__file__).with_name("grid20_distance.json")
# Target runs from the same stops and seed: each stops within its target.
TARGETS = {
    "grid20_ratio.json": Target(kind="ratio", value=6),
    "grid20_duration.json": Target(kind="duration", value=2400),
}
# Mode runs with the same seed: (the request's fields, the stops, the lattice's primary roads).
MODES: dict[str, tuple[dict[str, Any], tuple[int, ...], int]] = {
    "grid20_time.json": ({"objective": "time"}, (0, 210, 399), 4),
    "grid20_noleft.json": ({"constraints": {"no_left": True}}, (42, 210, 357), 0),
}
# The collect run's lattice: thirteen of its roads are bridges, nine east-west and four north-south.
BRIDGES = {
    (a, b): {"flags": EdgeFlag.BRIDGE}
    for a, b in [(r * 20 + c, r * 20 + c + 1) for r in (4, 9, 14) for c in (3, 9, 15)]
    + [(r * 20 + c, (r + 1) * 20 + c) for r in (6, 12) for c in (6, 13)]
}
# The pilgrimage's lattice: two brands, ids in name order, 0 Acme and 1 Pilgrim Coffee.
BRANDS = {
    "Pilgrim Coffee": [23, 57, 88, 125, 164, 177, 236, 248, 281, 305, 322, 347, 366, 392],
    "Acme": [45, 150, 260, 330, 380],
}
# The alphabet route's lattice: rows named after trees (T, J, A, M, E, C, ...: A on row 2), the
# columns numbered avenues but for column 8, Upas Street (U), and column 15, Willow Street (W).
ROW_LETTERS = "TJAMECSQGDLONBHKRPFI"
COLUMNS = [
    "Upas Street" if c == 8 else "Willow Street" if c == 15 else avenue(c + 1) for c in range(20)
]
LETTERED = named_roads(
    20, 20, [f"{TREES[ord(letter) - 65]} Street" for letter in ROW_LETTERS], COLUMNS
)
Backend = type[PythonBackend] | type[CsrBackend]


@pytest.mark.parametrize("backend_type", [PythonBackend, CsrBackend], ids=["python", "csr"])
def test_grid_route_is_golden(backend_type: Backend) -> None:
    g = lattice(20, 20)
    stops = [at_node(g, v) for v in (0, 210, 399)]
    request = RouteRequest(stops=stops, seed=7, max_iterations=400, budget_s=120)
    result = plan_route(g, request, backend=backend_type(g), check_delta=True)
    compare(GOLDEN, golden_of(g, request, result))


@pytest.mark.parametrize("name", sorted(TARGETS))
@pytest.mark.parametrize("backend_type", [PythonBackend, CsrBackend], ids=["python", "csr"])
def test_grid_target_route_is_golden(backend_type: Backend, name: str) -> None:
    g = lattice(20, 20)
    stops = [at_node(g, v) for v in (0, 210, 399)]
    request = RouteRequest(stops=stops, seed=7, budget_s=120, target=TARGETS[name])
    result = plan_route(g, request, backend=backend_type(g), check_delta=True)
    assert result.run.stopped_because == "target"
    actual = golden_of(g, request, result)
    actual["time_s"] = result.route.metrics.time_s
    actual["stopped_because"] = result.run.stopped_because
    compare(Path(__file__).with_name(name), actual)


@pytest.mark.parametrize("name", sorted(MODES))
@pytest.mark.parametrize("backend_type", [PythonBackend, CsrBackend], ids=["python", "csr"])
def test_grid_mode_route_is_golden(backend_type: Backend, name: str) -> None:
    fields, nodes, primary_every = MODES[name]
    g = lattice(20, 20, primary_every=primary_every)
    stops = [at_node(g, v) for v in nodes]
    request = RouteRequest(stops=stops, seed=7, max_iterations=400, budget_s=120, **fields)
    result = plan_route(g, request, backend=backend_type(g), check_delta=True)
    actual = golden_of(g, request, result)
    actual["time_s"] = result.route.metrics.time_s
    actual["delay_s"] = result.route.metrics.delay_s
    actual["turns"] = count_turns(g, result.route.edges)
    compare(Path(__file__).with_name(name), actual)


@pytest.mark.parametrize("backend_type", [PythonBackend, CsrBackend], ids=["python", "csr"])
def test_grid_collect_route_is_golden(backend_type: Backend) -> None:
    g = lattice(20, 20, roads=BRIDGES)
    stops = [at_node(g, v) for v in (0, 210, 399)]
    request = RouteRequest(
        stops=stops,
        seed=7,
        max_iterations=400,
        budget_s=120,
        objective="collect",
        item="bridge",
    )
    result = plan_route(g, request, backend=backend_type(g), check_delta=True)
    actual = golden_of(g, request, result)
    actual["items"] = result.route.metrics.counters["items"]
    compare(Path(__file__).with_name("grid20_collect_bridge.json"), actual)


@pytest.mark.parametrize("backend_type", [PythonBackend, CsrBackend], ids=["python", "csr"])
def test_grid_brand_route_is_golden(backend_type: Backend) -> None:
    g = lattice(20, 20, brands=BRANDS)
    stops = [at_node(g, v) for v in (0, 210, 399)]
    request = RouteRequest(
        stops=stops, seed=7, max_iterations=400, budget_s=120, objective="brand", brand=1
    )
    result = plan_route(g, request, backend=backend_type(g), check_delta=True)
    actual = golden_of(g, request, result)
    actual["brand"] = result.route.metrics.counters["brand"]
    compare(Path(__file__).with_name("grid20_brand.json"), actual)


@pytest.mark.parametrize("backend_type", [PythonBackend, CsrBackend], ids=["python", "csr"])
def test_grid_alphabet_route_is_golden(backend_type: Backend) -> None:
    g = lattice(20, 20, roads=LETTERED)
    stops = [at_node(g, v) for v in (0, 399)]
    request = RouteRequest(
        stops=stops, seed=7, max_iterations=20, budget_s=120, objective="alphabet"
    )
    result = plan_route(g, request, backend=backend_type(g))
    actual = golden_of(g, request, result)
    actual["alphabet"] = result.route.metrics.counters["alphabet"]
    compare(Path(__file__).with_name("grid20_alphabet.json"), actual)


def golden_of(g: GraphData, request: RouteRequest, result: PlanResult) -> dict[str, Any]:
    route = result.route
    return {
        "graph_version": g.meta["graph_version"],
        "request": request.model_dump(mode="json"),
        "nodes": route.nodes.tolist(),
        "edges": route.edges.tolist(),
        "stop_idx": list(route.stop_idx),
        "length_m": route.metrics.length_m,
        "attempts": result.run.attempts,
        "accepted": result.run.accepted,
    }


def compare(path: Path, actual: dict[str, Any]) -> None:
    if os.environ.get("WORST_ROUTE_REGEN_GOLDEN") == "1":
        path.write_text(json.dumps(actual, indent=1) + "\n")
        pytest.skip(f"rewrote {path.name}")

    golden = json.loads(path.read_text())
    assert golden["graph_version"] == actual["graph_version"]
    assert golden["request"] == actual["request"]
    assert (
        np.array(golden["nodes"], dtype=np.int32).tobytes() == np.int32(actual["nodes"]).tobytes()
    )
    assert (
        np.array(golden["edges"], dtype=np.int32).tobytes() == np.int32(actual["edges"]).tobytes()
    )
    assert golden == actual


@pytest.mark.parametrize("backend_type", [PythonBackend, CsrBackend], ids=["python", "csr"])
def test_grid_hard_route_is_golden(backend_type: Backend) -> None:
    g = lattice(20, 20, named=True)
    stops = [at_node(g, v) for v in (0, 210, 399)]
    request = RouteRequest(
        stops=stops, seed=7, max_iterations=400, budget_s=120, constraints={"hard": True}
    )
    result = plan_route(g, request, backend=backend_type(g), check_delta=True)
    assert result.report.ok
    compare(Path(__file__).with_name("grid20_hard.json"), golden_of(g, request, result))
