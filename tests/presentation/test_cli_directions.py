"""The presentation in the route.json `plan --out` writes, which `read_route`, `validate` and
`plan --base` read as before, and `worst-route directions` on it, on a lattice build."""

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
from .support import write_fixture_places

runner = CliRunner()
GRID = lattice(10, 10)


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


def test_plan_writes_the_presentation_into_route_json(data_dir: Path, tmp_path: Path) -> None:
    write_fixture_places(data_dir)  # over the same lattice
    g, out = planned(data_dir, tmp_path)
    doc = json.loads(out.read_text())
    saved = read_route(out)
    m = doc["metrics"]
    steps = route_directions(
        g, saved.nodes, saved.edges, saved.stop_idx, labels=[None] * 3, seed=3, ratio=m["ratio"]
    )
    assert doc["directions"] == [s.as_dict() for s in steps]
    assert doc["toasts"][0] == f"{m['ratio']:.1f}× less efficient than the sensible route."
    assert m["extra_fuel_gal"] is None and m["extra_cost_usd"] is None  # no vehicle in the CLI
    assert m["states"] == ["Westland", "Eastland"] and m["towns_n"] == len(m["towns"]) > 0
    assert 0 < m["network_fraction"] <= 1
    # what read the file before reads it still: validate, and --base continuing it
    result = runner.invoke(app, ["validate", str(out)])
    assert result.exit_code == 0 and result.stdout.splitlines()[-1] == "valid", result.output
    more = tmp_path / "more.json"
    result = runner.invoke(
        app,
        ["plan", "--region", "portland", "--base", str(out), "--seed", "4",
         "--max-iterations", "100", "--out", str(more)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert json.loads(more.read_text())["directions"][0]["kind"] == "depart"
    # the directions command prints the file's directions
    printed = runner.invoke(app, ["directions", str(out)]).stdout.splitlines()
    assert [line.split(". ", 1)[1].split("  (")[0] for line in printed] == [
        s["text"] for s in doc["directions"]
    ]


def test_without_a_places_index_states_and_towns_are_null(data_dir: Path, tmp_path: Path) -> None:
    _, out = planned(data_dir, tmp_path)
    m = json.loads(out.read_text())["metrics"]
    assert m["states"] is None and m["towns"] is None and m["towns_n"] is None
