"""`tools/border_ends.py` on the two border extracts: alone, each cuts the highway off at a dead end
beyond the state line; merged, no dead end lies outside the region, and the cut ends continue."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import geopandas as gpd
import pytest
import shapely

from worst_route.config import REGIONS, STATES, Region
from worst_route.pipeline import layout
from worst_route.pipeline.filter import filter_roads

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "osm"


def tool() -> ModuleType:
    path = ROOT / "tools" / "border_ends.py"
    spec = importlib.util.spec_from_file_location("border_ends", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def states_file(tmp_path: Path) -> Path:
    """Two states split along the fixtures' line, lat 45.5: Southland (91) and Northland (92)."""
    path = tmp_path / "states.geojson"
    frame = gpd.GeoDataFrame(
        {"STATEFP": ["91", "92"], "STUSPS": ["SL", "NL"]},
        geometry=[shapely.box(-122.7, 45.4, -122.5, 45.5), shapely.box(-122.7, 45.5, -122.5, 45.6)],
        crs=4326,
    )
    frame.to_file(path, driver="GeoJSON")
    return path


@pytest.fixture
def regions(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`south` (Southland's extract) and `border` (both), filtered under the test's data dir."""
    monkeypatch.setitem(STATES, "southland", ("SL", "91"))
    monkeypatch.setitem(STATES, "northland", ("NL", "92"))
    for name, states, fixtures in [
        ("south", ("southland",), ["border_south"]),
        ("border", ("southland", "northland"), ["border_south", "border_north"]),
    ]:
        monkeypatch.setitem(REGIONS, name, Region(name=name, states=states, crs_epsg=32610))
        sources = [FIXTURES / f"{f}.osm" for f in fixtures]
        filter_roads(
            sources,
            layout.roads_pbf(name),
            layout.pois_parquet(name),
            layout.restrictions_parquet(name),
            None,
        )


def test_dead_ends_and_where_they_lie(regions: None, tmp_path: Path) -> None:
    module = tool()
    states = gpd.read_file(states_file(tmp_path))
    south, border = layout.roads_pbf("south"), layout.roads_pbf("border")
    assert module.dead_ends(south).tolist() == [9004, 9010]
    assert module.dead_ends(border).tolist() == [9010, 9020]
    assert module.outside_ends(south, states, {"91"}) == [module.End(9004, -122.6, 45.503, "NL")]
    assert module.outside_ends(border, states, {"91", "92"}) == []
    assert module.statuses(border, [9004, 9010, 777]) == {
        9004: "continues",
        9010: "ends",
        777: "absent",
    }


def test_the_report(regions: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "ends.json"
    argv = ["--region", "south", "--compare", "border", "--states", str(states_file(tmp_path))]
    assert tool().main([*argv, "--out", str(out)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "south: 2 dead ends; 1 outside its states (NL 1)",
        "in border: 1 continue, 0 still end, 0 absent",
        "  NL: 1 continue, 0 still end, 0 absent",
        f"wrote {out}",
    ]
    report = json.loads(out.read_text())
    assert report["outside"] == {"count": 1, "by_state": {"NL": 1}}
    assert report["compare"] == {"region": "border", "continues": 1}
    assert report["ends"] == [
        {"node": 9004, "lon": -122.6, "lat": 45.503, "usps": "NL", "status": "continues"}
    ]


def test_a_missing_input_is_one_line(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    states = str(states_file(tmp_path))
    assert tool().main(["--region", "oregon", "--states", states]) == 1
    assert capsys.readouterr().err == f"missing input {layout.roads_pbf('oregon')}\n"
