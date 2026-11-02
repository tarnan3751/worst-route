"""Loading saved builds for the API and its workers (`docs/reference/graph.md`, **Loading builds**).

`load_build(data_dir, region, build_id="latest", backend=...)` reads
`<data_dir>/graphs/<region>/<build_id>/` with `store.read_build`, every array memory-mapped, so
worker processes share one copy through the OS page cache, and returns it as `Loaded`: the
`GraphData` and the build's directory. Its `SpatialIndex` (`Loaded.spatial`) and the backend
`backend` names (`Loaded.backend`, `make_backend`) are made on first use and kept: the CSR
backend's first imports the Numba kernels, which compile then when their cache does not hold them
(a first start), and the API process makes both in a thread once it starts (`api/app.py`), so it
listens without waiting for them. Two threads that ask at once may each make one; either serves.
`Loaded.bbox` is its area (its `params.bbox`, else its nodes' extent), worked out once.
`Loaded.geometry` reads the build's geometry (`graph/geometry.py`) on first use,
memory-mapped: the API process draws routes with it, a job's worker never touches it. "latest"
is the id in `graphs/<region>/latest`, read on every call. The two builds used last stay
loaded, keyed by data directory, region, build id and backend. A build that is not there, or
one saved before an array of the format existed (`store.MissingArray`), raises
`BuildNotFound`, naming the pipeline step to run.
"""

from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from worst_route.config import DEFAULT_BACKEND, BackendName
from worst_route.graph.backend import GraphBackend
from worst_route.graph.backends import make_backend
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.store import MissingArray, read_build

if TYPE_CHECKING:
    from worst_route.graph.geometry import Geometry


class BuildNotFound(FileNotFoundError):  # noqa: N818 (reads as the condition)
    """No saved build where one was asked for."""


@dataclass(frozen=True, eq=False)
class Loaded:
    """One saved build, ready to plan on."""

    data: GraphData
    path: Path
    backend_name: BackendName = DEFAULT_BACKEND

    @cached_property
    def spatial(self) -> SpatialIndex:
        """The build's spatial index, made on first use."""
        return SpatialIndex(self.data)

    @cached_property
    def backend(self) -> GraphBackend:
        """The backend `backend_name` names, made on first use (the CSR backend's first imports
        the Numba kernels, compiling them when their cache does not hold them)."""
        return make_backend(self.data, self.backend_name)

    @cached_property
    def bbox(self) -> tuple[float, float, float, float]:
        """The build's area, (west, south, east, north): its `params.bbox`, else its nodes'
        extent, worked out on first use."""
        bbox = self.data.meta["params"].get("bbox")
        if bbox is not None:
            west, south, east, north = (float(v) for v in bbox)
            return west, south, east, north
        lon, lat = self.data.node_lon, self.data.node_lat
        return float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())

    @cached_property
    def geometry(self) -> "Geometry":
        """The build's geometry, read on first use; `MissingArray` when its files are gone."""
        from worst_route.graph.geometry import read_geometry  # never in a job's worker

        return read_geometry(self.path, self.data.m)


def load_build(
    data_dir: Path, region: str, build_id: str = "latest", *, backend: BackendName = DEFAULT_BACKEND
) -> Loaded:
    """The build `build_id` of `region` under `data_dir` ("latest": the region's current one)."""
    hint = f"run `worst-route pipeline save --region {region}` first"
    if build_id == "latest":
        latest = data_dir / "graphs" / region / "latest"
        if not latest.exists():
            raise BuildNotFound(f"no build of {region} under {data_dir}: {hint}")
        build_id = latest.read_text().strip()
    return _load(data_dir, region, build_id, backend)


@lru_cache(maxsize=2)
def _load(data_dir: Path, region: str, build_id: str, backend: BackendName) -> Loaded:
    path = data_dir / "graphs" / region / build_id
    if not (path / "meta.json").exists():
        raise BuildNotFound(
            f"no saved build at {path}: run `worst-route pipeline save --region {region}` first"
        )
    try:
        data = read_build(path)
    except MissingArray as error:
        raise BuildNotFound(str(error)) from None
    return Loaded(data, path, backend)
