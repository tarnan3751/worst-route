from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from worst_route.graph.data import GraphData
from worst_route.graph.loader import BuildNotFound, load_build
from worst_route.graph.store import write_build
from worst_route.graph.testing import grid_graph


def save(data_dir: Path, build_id: str, rows: int = 3) -> GraphData:
    g = grid_graph(rows, 3, 100)
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": build_id})
    write_build(g, data_dir / "graphs" / "portland" / build_id)
    return g


def test_latest_names_the_region_s_current_build(tmp_path: Path) -> None:
    save(tmp_path, "b1")
    save(tmp_path, "b2", rows=4)
    (tmp_path / "graphs" / "portland" / "latest").write_text("b2\n")
    loaded = load_build(tmp_path, "portland", backend="python")
    assert loaded.data.meta["build_id"] == "b2" and loaded.data.n == 12
    assert loaded is load_build(tmp_path, "portland", "b2", backend="python")
    assert loaded.backend.name == "python" and loaded.spatial.data is loaded.data
    assert isinstance(loaded.data.edge_u, np.memmap)  # mapped, not read


def test_the_two_builds_used_last_stay_loaded(tmp_path: Path) -> None:
    for b in ("b1", "b2", "b3"):
        save(tmp_path, b)
    first = load_build(tmp_path, "portland", "b1", backend="python")
    load_build(tmp_path, "portland", "b2", backend="python")
    assert load_build(tmp_path, "portland", "b1", backend="python") is first
    load_build(tmp_path, "portland", "b3", backend="python")  # evicts b2, the least recent
    assert load_build(tmp_path, "portland", "b1", backend="python") is first
    assert load_build(tmp_path, "portland", "b1", backend="csr") is not first


def test_a_missing_build_names_the_step_to_run(tmp_path: Path) -> None:
    with pytest.raises(BuildNotFound, match="no build of portland .*pipeline save"):
        load_build(tmp_path, "portland")
    save(tmp_path, "b1")
    with pytest.raises(BuildNotFound, match="no saved build at .*b9"):
        load_build(tmp_path, "portland", "b9")
