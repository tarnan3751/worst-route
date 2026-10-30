"""Loading saved builds for the API and its workers (`docs/reference/graph.md`, **Loading builds**).

`load_build(data_dir, region, build_id="latest", backend=...)` reads
`<data_dir>/graphs/<region>/<build_id>/` with `store.read_build`, every array memory-mapped, so
worker processes share one copy through the OS page cache, and returns it as `Loaded`: the
`GraphData`, its `SpatialIndex` and the backend `backend` names (`make_backend`). "latest" is the
id in `graphs/<region>/latest`, read on every call. The two builds used last stay loaded, keyed
by data directory, region, build id and backend. A build that is not there, or one saved before
an array of the format existed (`store.MissingArray`), raises `BuildNotFound`, naming the
pipeline step to run.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from worst_route.config import DEFAULT_BACKEND, BackendName
from worst_route.graph.backend import GraphBackend
from worst_route.graph.backends import make_backend
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.store import MissingArray, read_build


class BuildNotFound(FileNotFoundError):  # noqa: N818 (reads as the condition)
    """No saved build where one was asked for."""


@dataclass(frozen=True, eq=False)
class Loaded:
    """One saved build, ready to plan on."""

    data: GraphData
    spatial: SpatialIndex
    backend: GraphBackend


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
    return Loaded(data, SpatialIndex(data), make_backend(data, backend))
