import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from worst_route.config import PIPELINE_VERSION
from worst_route.graph.data import ARRAY_SPECS, GraphData, Strings
from worst_route.graph.store import MissingArray, graph_version, read_build, write_build
from worst_route.graph.testing import Edge, grid_graph, make_graph


def small_build() -> GraphData:
    """Three nodes, four edges, a twin pair and a transition, with lon/lat given."""
    return make_graph(
        [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)],
        [(0, 1, 100.0), (1, 2, 100.0), (2, 0, 141.0), (1, 0, 100.0)],
        twins=[((0, 1), (1, 0))],
        transitions=[((0, 1), (1, 2))],
        lonlat=[(-122.0, 45.0), (-121.99875, 45.0), (-121.99875, 45.0009)],
    )


def named_roads() -> GraphData:
    nodes = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]
    edges = [Edge(0, 1, name="Hawthorne Boulevard"), Edge(1, 2, ref="US 26", name="Powell")]
    return make_graph(nodes, edges, transitions=[((0, 1), (1, 2))])


def branded() -> GraphData:
    """`small_build` with two brands, one with a wikidata id and two POIs at a node."""
    g = make_graph(
        [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)],
        [(0, 1, 100.0), (1, 2, 100.0), (2, 0, 141.0), (1, 0, 100.0)],
        brands={"Café Ünïcode": [0, 2], "Plaid Pantry": [1]},
    )
    strings = replace(g.strings, brand_wikidata=["Q1", None])
    locations = np.array([1, 2, 1], dtype=np.int32)
    locations.flags.writeable = False
    g = replace(g, strings=strings, brand_locations=locations, meta=dict(g.meta))
    g.meta["graph_version"] = graph_version(g)
    return g


def assert_same_build(a: GraphData, b: GraphData) -> None:
    for name in ARRAY_SPECS:
        x, y = getattr(a, name), getattr(b, name)
        assert x.dtype == y.dtype, name
        assert np.array_equal(x, y), name
    assert a.strings == b.strings
    assert a.meta == b.meta


def test_graph_version_is_pinned() -> None:
    # Freezes the version definition and the array order (docs/reference/graph.md): this
    # literal changes only under a tier A brief that changes the build format.
    expected = "281d156d6db24a54f6e4b3140b720d21b15c55f51865b2139f51cce42d29d68f"
    assert small_build().meta["graph_version"] == expected


@pytest.mark.parametrize("mmap", [True, False])
@pytest.mark.parametrize(
    "build", [small_build, named_roads, branded, lambda: grid_graph(4, 3, 80.0)]
)
def test_round_trip(tmp_path: Path, build, mmap: bool) -> None:
    g = build()
    version = write_build(g, tmp_path / "b")
    assert version == g.meta["graph_version"]
    back = read_build(tmp_path / "b", mmap=mmap)
    assert_same_build(g, back)
    for name in ARRAY_SPECS:
        a = getattr(back, name)
        assert isinstance(a, np.memmap) is mmap, name
        assert not a.flags.writeable, name


def test_files_on_disk(tmp_path: Path) -> None:
    g = named_roads()
    write_build(g, tmp_path / "b")
    assert sorted(p.name for p in (tmp_path / "b").iterdir()) == [
        "arrays",
        "meta.json",
        "strings.json",
    ]
    assert sorted(p.name for p in (tmp_path / "b" / "arrays").iterdir()) == sorted(
        f"{name}.npy" for name in ARRAY_SPECS
    )
    strings = json.loads((tmp_path / "b" / "strings.json").read_text(encoding="utf-8"))
    assert strings == {
        "names": ["Hawthorne Boulevard", "Powell"],
        "refs": ["US 26"],
        "identities": ["Hawthorne Boulevard", "US 26"],
        "brands": [],
        "brand_wikidata": [],
    }
    meta = json.loads((tmp_path / "b" / "meta.json").read_text())
    assert meta["graph_version"] == g.meta["graph_version"]
    assert meta["counts"] == {"nodes": 3, "edges": 2}
    loaded = np.load(tmp_path / "b" / "arrays" / "coords.npy", allow_pickle=False)
    assert loaded.dtype == np.float32 and loaded.shape == (4, 2)


def test_version_reproduced_by_a_second_write(tmp_path: Path) -> None:
    g = grid_graph(3, 3, 100.0)
    first = write_build(g, tmp_path / "one")
    second = write_build(g, tmp_path / "two")
    assert first == second == g.meta["graph_version"]
    assert read_build(tmp_path / "two").meta["graph_version"] == first
    rebuilt = grid_graph(3, 3, 100.0)
    assert graph_version(rebuilt) == first


def test_version_ignores_meta_other_than_params() -> None:
    g = small_build()
    other = replace(
        g,
        meta={
            **g.meta,
            "created": "2026-10-30T12:00:00+00:00",
            "region": "elsewhere",
            "build_id": "b2",
            "pipeline_version": "9",
            "sources": [{"file": "x.osm.pbf"}],
            "graph_version": "stale",
        },
    )
    assert graph_version(other) == graph_version(g)


def test_version_changes_with_arrays_strings_and_params() -> None:
    g = small_build()
    base = graph_version(g)
    lengths = g.edge_length_m.copy()
    lengths[0] += 1.0
    assert graph_version(replace(g, edge_length_m=lengths)) != base
    mask = g.tier_node_mask.copy()
    mask[0, 0] = True
    assert graph_version(replace(g, tier_node_mask=mask)) != base
    assert graph_version(replace(g, strings=Strings(["Main"], [], []))) != base
    assert graph_version(replace(g, meta={**g.meta, "params": {"tolerance_m": 8}})) != base


def test_version_changes_with_the_brands() -> None:
    g = branded()
    base = graph_version(g)
    nodes = g.brand_nodes.copy()
    nodes[0] = 1
    assert graph_version(replace(g, brand_nodes=nodes)) != base
    locations = g.brand_locations.copy()
    locations[1] = 1
    assert graph_version(replace(g, brand_locations=locations)) != base
    for strings in (
        replace(g.strings, brands=["Café Unicode", "Plaid Pantry"]),
        replace(g.strings, brand_wikidata=["Q2", None]),
        replace(g.strings, brand_wikidata=[None, None]),
    ):
        assert graph_version(replace(g, strings=strings)) != base


def test_version_ignores_the_order_of_params_keys() -> None:
    g = small_build()
    one = replace(g, meta={**g.meta, "params": {"a": 1, "b": {"c": 2, "d": 3}}})
    two = replace(g, meta={**g.meta, "params": {"b": {"d": 3, "c": 2}, "a": 1}})
    assert graph_version(one) == graph_version(two)


def test_write_into_an_existing_build_directory(tmp_path: Path) -> None:
    # the pipeline's earlier steps leave stages/ and sidecars in the build directory
    build = tmp_path / "portland" / "b1"
    (build / "stages").mkdir(parents=True)
    (build / "stages" / "build.pkl").write_bytes(b"stage")
    (build / "stages" / "build.json").write_text("{}")
    g = small_build()
    assert write_build(g, build) == g.meta["graph_version"]
    assert sorted(p.name for p in build.iterdir()) == [
        "arrays",
        "meta.json",
        "stages",
        "strings.json",
    ]
    assert (build / "stages" / "build.pkl").read_bytes() == b"stage"
    assert_same_build(g, read_build(build))
    assert [p.name for p in (tmp_path / "portland").iterdir()] == ["b1"]


def test_write_refuses_a_written_build(tmp_path: Path) -> None:
    g = small_build()
    write_build(g, tmp_path / "b")
    with pytest.raises(FileExistsError, match="meta.json"):
        write_build(g, tmp_path / "b")


def test_write_refuses_a_directory_with_meta(tmp_path: Path) -> None:
    build = tmp_path / "b"
    build.mkdir()
    (build / "meta.json").write_text("{}")
    with pytest.raises(FileExistsError, match="meta.json"):
        write_build(small_build(), build)
    assert [p.name for p in build.iterdir()] == ["meta.json"]
    assert (build / "meta.json").read_text() == "{}"


def test_arrays_and_strings_without_meta_are_replaced(tmp_path: Path) -> None:
    # what a write that crashed between its moves leaves behind
    build = tmp_path / "b"
    (build / "arrays").mkdir(parents=True)
    (build / "arrays" / "junk.npy").write_bytes(b"junk")
    (build / "arrays" / "edge_u.npy").write_bytes(b"stale")
    (build / "strings.json").write_text("stale")
    (build / "stages").mkdir()
    g = named_roads()
    write_build(g, build)
    assert sorted(p.name for p in (build / "arrays").iterdir()) == sorted(
        f"{name}.npy" for name in ARRAY_SPECS
    )
    assert json.loads((build / "strings.json").read_text())["names"] == g.strings.names
    assert (build / "stages").is_dir()
    assert_same_build(g, read_build(build))


def test_a_crash_while_staging_leaves_the_directory_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = tmp_path / "b"
    (build / "stages").mkdir(parents=True)
    saved = []
    real_save = np.save

    def crash_on_the_fifth(file: Path, arr: np.ndarray, allow_pickle: bool) -> None:
        if len(saved) == 4:
            raise OSError("disk full")
        saved.append(file)
        real_save(file, arr, allow_pickle=allow_pickle)

    with monkeypatch.context() as patched:
        patched.setattr("worst_route.graph.store.np.save", crash_on_the_fifth)
        with pytest.raises(OSError, match="disk full"):
            write_build(small_build(), build)
    assert [p.name for p in build.iterdir()] == ["stages"]
    with pytest.raises(FileNotFoundError):
        read_build(build)
    g = small_build()
    write_build(g, build)
    assert_same_build(g, read_build(build))
    assert [p.name for p in tmp_path.iterdir()] == ["b"]


def test_a_crash_between_the_moves_leaves_no_meta_and_the_next_write_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # meta.json moves in last, so readers never see half a build; the next write replaces
    # what the crashed one left
    build = tmp_path / "b"
    real_replace = Path.replace

    def crash_on_meta(self: Path, target: Path) -> Path:
        if Path(target).name == "meta.json":
            raise OSError("power cut")
        return real_replace(self, target)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "replace", crash_on_meta)
        with pytest.raises(OSError, match="power cut"):
            write_build(small_build(), build)
    assert sorted(p.name for p in build.iterdir()) == ["arrays", "strings.json"]
    with pytest.raises(FileNotFoundError, match="meta.json"):
        read_build(build)
    g = named_roads()
    assert write_build(g, build) == g.meta["graph_version"]
    assert_same_build(g, read_build(build))
    assert [p.name for p in tmp_path.iterdir()] == ["b"]


def test_write_validates_first(tmp_path: Path) -> None:
    g = small_build()
    broken = replace(g, edge_u=g.edge_u[::-1].copy(), edge_v=g.edge_v[::-1].copy())
    with pytest.raises(ValueError, match="sorted"):
        write_build(broken, tmp_path / "b")
    assert list(tmp_path.iterdir()) == []


def test_write_replaces_a_crashed_temporary(tmp_path: Path) -> None:
    (tmp_path / ".b.tmp" / "arrays").mkdir(parents=True)
    (tmp_path / ".b.tmp" / "arrays" / "junk.npy").write_bytes(b"junk")
    write_build(small_build(), tmp_path / "b")
    assert [p.name for p in tmp_path.iterdir()] == ["b"]
    assert "junk.npy" not in [p.name for p in (tmp_path / "b" / "arrays").iterdir()]


def test_read_rejects_a_missing_array(tmp_path: Path) -> None:
    g = small_build()  # this pipeline's build, so the line names the array
    write_build(replace(g, meta={**g.meta, "pipeline_version": PIPELINE_VERSION}), tmp_path / "b")
    (tmp_path / "b" / "arrays" / "twin_edges.npy").unlink()
    with pytest.raises(FileNotFoundError, match="twin_edges"):
        read_build(tmp_path / "b")


def test_read_refuses_a_build_another_pipeline_version_wrote(tmp_path: Path) -> None:
    # Every array present, but written by pipeline 3: this code does not read it.
    g = small_build()
    write_build(replace(g, meta={**g.meta, "pipeline_version": PIPELINE_VERSION}), tmp_path / "b")
    meta = json.loads((tmp_path / "b" / "meta.json").read_text())
    (tmp_path / "b" / "meta.json").write_text(json.dumps({**meta, "pipeline_version": "3"}))
    with pytest.raises(MissingArray) as refused:
        read_build(tmp_path / "b")
    assert str(refused.value) == (
        f"the build at {tmp_path / 'b'} was written by pipeline 3 (now {PIPELINE_VERSION}): "
        "rebuild the region with `worst-route pipeline filter --region test` and the steps "
        "after it"
    )


@pytest.mark.parametrize("version", [PIPELINE_VERSION, "1"])
def test_a_build_without_an_array_names_its_remedy(tmp_path: Path, version: str) -> None:
    # This pipeline's build lacking an array is saved again; another version's is rebuilt from
    # filter, under a new build id (region "test").
    g = small_build()
    write_build(replace(g, meta={**g.meta, "pipeline_version": version}), tmp_path / "b")
    (tmp_path / "b" / "arrays" / "node_roundabouts.npy").unlink()
    with pytest.raises(MissingArray) as missing:
        read_build(tmp_path / "b")
    if version == PIPELINE_VERSION:
        assert str(missing.value) == (
            f"the build at {tmp_path / 'b'} lacks the array node_roundabouts: rebuild it with "
            "`worst-route pipeline save --force --region test`"
        )
    else:
        assert str(missing.value) == (
            f"the build at {tmp_path / 'b'} was written by pipeline 1 (now {PIPELINE_VERSION}): "
            "rebuild the region with `worst-route pipeline filter --region test` and the steps "
            "after it"
        )


def test_read_rejects_a_wrong_dtype(tmp_path: Path) -> None:
    g = small_build()
    write_build(g, tmp_path / "b")
    np.save(tmp_path / "b" / "arrays" / "edge_u.npy", g.edge_u.astype(np.int64))
    with pytest.raises(ValueError, match="edge_u: dtype"):
        read_build(tmp_path / "b")


def test_read_rejects_a_wrong_shape(tmp_path: Path) -> None:
    g = small_build()
    write_build(g, tmp_path / "b")
    np.save(tmp_path / "b" / "arrays" / "node_x.npy", g.node_x[:-1])
    with pytest.raises(ValueError, match="node_x: shape"):
        read_build(tmp_path / "b", mmap=False)
