"""Choosing a backend (`docs/reference/graph.md`, **Choosing a backend**).

`make_backend(data, name)` makes the `GraphBackend` a name asks for: "csr" (`CsrBackend`, the
Numba kernels), "python" (`PythonBackend`, the reference) or "auto", which is csr when numba
imports and python otherwise. Both give the same results, so the choice changes only the
speed; the backend's `name` says which one was made. `csr_backend` is imported only when it is
asked for, so the reference never needs numba. `backend_name(name)` says which one a name makes
without making it: auto is csr when numba imports.
"""

from worst_route.config import DEFAULT_BACKEND, BackendName
from worst_route.graph.backend import GraphBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend


def backend_name(name: BackendName = DEFAULT_BACKEND) -> str:
    """The `name` of the backend `make_backend` makes for `name`, without making it: auto is csr
    when numba imports, else python. It imports numba for csr and auto (not the kernels, so
    nothing compiles), so a numba installed but broken counts as none; csr's `ImportError` passes
    through, as `make_backend`'s does."""
    if name == "python":
        return name
    try:
        import numba  # noqa: F401
    except ImportError:
        if name == "csr":
            raise
        return "python"
    return "csr"


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
