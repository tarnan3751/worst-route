"""`tools/parity.py`: its steps on a fixture's filtered roads make builds its comparison finds the
same, the geometry with them; an array whose shape differs between two builds is reported as such
(not a crash comparing it), with whatever else differs, and the run is not the same."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from typer.testing import CliRunner

from tests.pipeline.chain import FIXTURES
from worst_route.cli import app
from worst_route.graph.store import write_build
from worst_route.graph.testing import grid_graph

ROOT = Path(__file__).parents[2]


def parity() -> ModuleType:
    spec = importlib.util.spec_from_file_location("parity", ROOT / "tools" / "parity.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_array_of_another_shape_is_reported(tmp_path: Path) -> None:
    for engine, data in (
        ("networkx", grid_graph(3, 3, 100.0)),
        ("arrays", grid_graph(3, 4, 100.0)),
    ):
        write_build(data, tmp_path / engine)
        (tmp_path / engine / "stages").mkdir()
        for name in ("cleanup", "enrich", "tiers"):
            (tmp_path / engine / "stages" / f"{name}.json").write_text(json.dumps({"seconds": 0}))
    tool = parity()
    result = tool.compare(tmp_path, ["networkx", "arrays"])
    found = result["engines"]["arrays"]
    shapes = {a["array"]: a for a in found["arrays_differing"]}
    assert "shape differs" in shapes["edge_u"] and "shape differs" in shapes["node_x"]
    lines, same = tool.summary(result, ["networkx", "arrays"])
    assert not same and "edge_u (<i4[24] against <i4[34])" in lines[0]


def test_the_steps_make_the_same_build(data_dir: Path, tmp_path: Path) -> None:
    """Each engine's build, cleanup and save, as the tool's children run them: the save step
    writes the build and its geometry as `pipeline save` does, and the two are the same."""
    source = str(FIXTURES / "divided.osm")
    filtered = CliRunner().invoke(
        app, ["pipeline", "filter", "--region", "portland", "--source", source]
    )
    assert filtered.exit_code == 0, filtered.output
    tool, work = parity(), tmp_path / "work"
    for engine in ("networkx", "arrays"):
        for step in tool.STEPS:
            tool.run_step(engine, step, "portland", work, True)
        assert (work / engine / "meta.json").exists() and (work / engine / "geometry").is_dir()
    lines, same = tool.summary(tool.compare(work, ["networkx", "arrays"]), ["networkx", "arrays"])
    assert same, lines
