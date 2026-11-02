"""The app without scipy, as the desktop app runs it (its spec leaves scipy out; only the
pipeline uses it): a process that refuses to import scipy loads the app's modules, indexes a
build, snaps and draws bubbles, labels the components on both backends, and plans a route on the
CSR kernels and a certified one, each validated. Numba tries scipy at its import and goes on
without it, as in the frozen app."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = Path(__file__).parents[2] / "packaging" / "desktop" / "worst-route-desktop.spec"
WITHOUT_SCIPY = """
import sys


class NoScipy:
    def find_spec(self, name, path=None, target=None):
        if name == "scipy" or name.startswith("scipy."):
            raise ImportError(f"no {name} in this process")
        return None


sys.meta_path.insert(0, NoScipy())

import numpy as np

import worst_route.api.app, worst_route.api.worker, worst_route.cli
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import grid_graph

g = grid_graph(5, 5, 100.0)
index = SpatialIndex(g)
assert index.snap(float(g.node_lat[12]), float(g.node_lon[12]), "via").node == 12
assert index.bubble_mask(np.array([[g.node_x[12], g.node_y[12]]]), 100.0).sum() == 5
assert PythonBackend(g).largest_scc_mask().all() and CsrBackend(g).largest_scc_mask().all()
stops = [(float(g.node_lat[v]), float(g.node_lon[v])) for v in (0, 24)]
plain = RouteRequest(stops=stops, seed=1, max_iterations=200)
certified = RouteRequest(stops=stops, seed=1, certified=True, budget_s=10)
for request in (plain, certified):
    assert plan_route(g, request, backend=CsrBackend(g)).report.ok
print(sorted(name for name in sys.modules if name.split(".")[0] == "scipy"))
"""


@pytest.mark.slow
def test_the_app_plans_in_a_process_without_scipy() -> None:
    run = subprocess.run(
        [sys.executable, "-c", WITHOUT_SCIPY], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "[]"


def test_the_desktop_app_leaves_scipy_out() -> None:
    # the spec runs only under PyInstaller: its statements that make EXCLUDES, and the names
    # those read, run on their own
    spec = ast.parse(SPEC.read_text())
    wanted = {"EXCLUDES", "PLATFORMS", "OWN", "MACOS", "LINUX"}
    making = [
        node
        for node in spec.body
        if isinstance(node, ast.Assign | ast.AugAssign)
        and wanted
        & {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    ]
    namespace: dict[str, object] = {"sys": sys}
    exec(compile(ast.Module(body=making, type_ignores=[]), str(SPEC), "exec"), namespace)
    excludes = namespace["EXCLUDES"]
    assert isinstance(excludes, list) and "scipy" in excludes
