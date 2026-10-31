"""`worst-route directions` on a route.json that `plan` wrote, on a lattice build."""

import json
from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from worst_route.cli import app
from worst_route.engine.io import read_route
from worst_route.graph.data import GraphData
from worst_route.graph.store import write_build
from worst_route.presentation.document import route_directions

from ..engine.graphs import at_node, lattice

runner = CliRunner()
GRID = lattice(10, 10, rounded_lonlat=True)


def planned(data_dir: Path, tmp_path: Path) -> tuple[GraphData, Path]:
    g = replace(GRID, meta={**GRID.meta, "region": "portland", "build_id": "b1"})
    write_build(g, data_dir / "graphs" / "portland" / "b1")
    (data_dir / "graphs" / "portland" / "latest").write_text("b1\n")
    out = tmp_path / "route.json"
    stops = [",".join(map(str, at_node(g, v))) for v in (0, 45, 99)]
    result = runner.invoke(
        app,
        ["plan", "--region", "portland", "--from", stops[0], "--via", stops[1], "--to", stops[2],
         "--seed", "3", "--max-iterations", "300", "--out", str(out)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    return g, out


def test_directions_prints_the_route_s_steps_and_jokes(data_dir: Path, tmp_path: Path) -> None:
    g, out = planned(data_dir, tmp_path)
    result = runner.invoke(app, ["directions", str(out)])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    saved = read_route(out)
    steps = route_directions(
        g, saved.nodes, saved.edges, saved.stop_idx, labels=[None] * 3, seed=3,
        ratio=saved.document["metrics"]["ratio"],
    )  # fmt: skip
    assert len(lines) == len(steps)
    width = len(str(len(steps)))
    for line, step in zip(lines, steps, strict=True):
        assert line.startswith(f"{step.step:>{width}}. {step.text}")
        driving = step.edge_range[0] < step.edge_range[1]
        assert line.endswith(")") == driving
    assert lines[0].startswith(f"{1:>{width}}. Head ") and " km, " in lines[0]
    assert "Arrive at stop 1; continue" in result.stdout
    assert lines[-1].endswith("Arrive at your destination")
    assert any(s.kind == "joke" for s in steps)

    plain = runner.invoke(app, ["directions", str(out), "--no-jokes"])
    assert plain.exit_code == 0, plain.output
    texts = [line.split(". ", 1)[1].split("  (")[0] for line in plain.stdout.splitlines()]
    assert texts == [s.text for s in steps if s.kind != "joke"]


def test_directions_refuses_another_build_or_a_broken_file(data_dir: Path, tmp_path: Path) -> None:
    _, out = planned(data_dir, tmp_path)
    doc = json.loads(out.read_text())
    doc["graph_version"] = "0" * 64
    other = tmp_path / "other.json"
    other.write_text(json.dumps(doc))
    result = runner.invoke(app, ["directions", str(other)])
    assert result.exit_code == 1 and "was planned on graph_version 000000000000" in result.stderr
    del doc["edges"]
    other.write_text(json.dumps(doc))
    result = runner.invoke(app, ["directions", str(other)])
    assert result.exit_code == 1 and result.stderr.startswith(f"cannot read {other}: KeyError")
