from pathlib import Path

from worst_route.config import REGIONS, Region
from worst_route.pipeline import layout


def test_paths_under_the_data_dir(data_dir: Path) -> None:
    assert layout.raw_pbf("oregon") == data_dir / "raw" / "oregon-latest.osm.pbf"
    assert layout.raw_md5("oregon") == data_dir / "raw" / "oregon-latest.osm.pbf.md5"
    assert layout.roads_pbf("portland") == data_dir / "filtered" / "portland.roads.osm.pbf"
    assert layout.pois_parquet("portland") == data_dir / "filtered" / "portland.pois.parquet"
    build = layout.build_dir("portland", "abc123")
    assert build == data_dir / "graphs" / "portland" / "abc123"
    assert layout.stage_file(build, "build.pkl") == build / "stages" / "build.pkl"
    assert layout.latest_file("portland") == data_dir / "graphs" / "portland" / "latest"
    assert layout.sidecar(build / "x.pkl") == build / "x.pkl.inputs.json"


def test_build_id() -> None:
    portland = REGIONS["portland"]
    build_id = layout.build_id_for("0" * 32, portland)
    assert len(build_id) == 12 and int(build_id, 16) >= 0
    assert layout.build_id_for("0" * 32, portland) == build_id
    assert layout.build_id_for("1" * 32, portland) != build_id
    assert layout.build_id_for("0" * 32, REGIONS["oregon"]) != build_id
    moved = Region(
        name="portland", state="oregon", bbox=(-123.0, 45.2, -122.3, 45.7), crs_epsg=32610
    )
    assert layout.build_id_for("0" * 32, moved) != build_id
    reprojected = Region(name="portland", state="oregon", bbox=portland.bbox, crs_epsg=5070)
    assert layout.build_id_for("0" * 32, reprojected) != build_id
    same = Region(name="portland", state="oregon", bbox=portland.bbox, crs_epsg=32610)
    assert layout.build_id_for("0" * 32, same) == build_id


def test_sidecar(tmp_path: Path) -> None:
    output = tmp_path / "out.bin"
    record = {"inputs": [{"file": "a", "md5": "b"}], "params": {"bbox": (1.0, 2.0, 3.0, 4.0)}}
    assert not layout.sidecar_matches(output, record)  # no output, no sidecar
    output.write_bytes(b"x")
    assert not layout.sidecar_matches(output, record)  # no sidecar
    layout.write_sidecar(output, record)
    assert layout.sidecar_matches(output, record)  # the tuple compares equal to its JSON list
    assert not layout.sidecar_matches(output, {**record, "params": {"bbox": None}})
    output.unlink()
    assert not layout.sidecar_matches(output, record)  # sidecar without its output


def test_file_md5(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"worst route")
    assert layout.file_md5(path) == "a5f42835d84598517db5a5a0371f1ed0"
