"""Choosing a backend (`docs/reference/graph.md`, **Choosing a backend**).

`make_backend(data, name)` makes the `GraphBackend` a name asks for: "csr" (`CsrBackend`, the
Numba kernels), "python" (`PythonBackend`, the reference) or "auto", which is csr when numba
imports and python otherwise. Both give the same results, so the choice changes only the
speed; the backend's `name` says which one was made. `csr_backend` is imported only when it is
asked for, so the reference never needs numba.
"""

from worst_route.config import DEFAULT_BACKEND, BackendName
from worst_route.graph.backend import GraphBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend


def make_backend(data: GraphData, name: BackendName = DEFAULT_BACKEND) -> GraphBackend:
    """The backend `name` asks for, over `data`."""
    if name == "python":
        return PythonBackend(data)
    if name not in ("auto", "csr"):
        raise ValueError(f"unknown backend {name!r}")
    try:
        from worst_route.graph.csr_backend import CsrBackend
    except ImportError:
        if name == "csr":
            raise
        return PythonBackend(data)
    return CsrBackend(data)
