from pathlib import Path

import osmium
import pyarrow.parquet as pq
import pytest

from worst_route.config import REGIONS
from worst_route.pipeline.filter import (
    POI_SCHEMA,
    FilterStats,
    filter_roads,
    merge_parts,
    poi_kinds,
    source_timestamp,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
BBOX = (-122.605, 45.495, -122.595, 45.505)  # the box bbox.osm is drawn around
GRID_STREETS = list(range(2000, 2006)) + list(range(2100, 2106))
GRID_ROAD_NODES = [1000 + 10 * r + c for r in range(6) for c in range(6)] + [1100, 1101, 1102, 1103]


def run(
    tmp_path: Path,
    *names: str,
    bbox: tuple[float, float, float, float] | None = None,
    classes: frozenset[str] | None = None,
):
    roads, pois = tmp_path / "roads.osm.pbf", tmp_path / "pois.parquet"
    sources = [FIXTURES / f"{n}.osm" for n in names]
    stats = filter_roads(sources, roads, pois, tmp_path / "restrictions.parquet", bbox, classes)
    return stats, roads, pq.read_table(pois).to_pylist()


def contents(path: Path) -> tuple[list[int], list[int], dict[int, int]]:
    """Node ids, way ids, and the number of tags on each tagged node."""
    nodes, ways, tagged = [], [], {}
    for obj in osmium.FileProcessor(path):
        if obj.is_node():
            nodes.append(obj.id)
            if len(obj.tags):
                tagged[obj.id] = len(obj.tags)
        elif obj.is_way():
            ways.append(obj.id)
    return nodes, ways, tagged


def test_grid_keeps_every_street_and_the_nodes_they_use(tmp_path: Path) -> None:
    stats, roads, _ = run(tmp_path, "grid")
    nodes, ways, tagged = contents(roads)
    assert ways == GRID_STREETS  # not the railway, not the Fred Meyer building
    assert nodes == sorted(GRID_ROAD_NODES)  # not the railway's, the building's, or Waffle House
    assert tagged == {}  # node tags are stripped from the roads file
    assert stats == FilterStats(
        ways_read=12,
        ways_kept=12,
        pois={"brand": 2, "level_crossing": 1, "stop": 1, "toll_booth": 1, "traffic_signals": 1},
    )


def test_grid_pois(tmp_path: Path) -> None:
    _, _, rows = run(tmp_path, "grid")
    by_id = {(r["osm_type"], r["osm_id"], r["kind"]): r for r in rows}
    assert sorted(by_id) == [
        ("n", 1022, "traffic_signals"),
        ("n", 1033, "stop"),
        ("n", 1102, "toll_booth"),
        ("n", 1103, "level_crossing"),
        ("n", 1106, "brand"),
        ("w", 3001, "brand"),
    ]
    toll = by_id["n", 1102, "toll_booth"]
    assert (toll["lon"], toll["lat"]) == pytest.approx((-122.59680, 45.50090), abs=1e-7)
    assert toll["brand"] is None and toll["name"] is None
    crossing = by_id["n", 1103, "level_crossing"]
    assert (crossing["lon"], crossing["lat"]) == pytest.approx((-122.59808, 45.50450), abs=1e-7)
    waffle = by_id["n", 1106, "brand"]
    assert (waffle["brand"], waffle["brand_wikidata"], waffle["name"]) == (
        "Waffle House",
        "Q1701206",
        "Waffle House",
    )
    # The building is a closed square: its mean, counting the repeated corner once, is its centre.
    store = by_id["w", 3001, "brand"]
    assert (store["brand"], store["brand_wikidata"]) == ("Fred Meyer", "Q5495932")
    assert (store["lon"], store["lat"]) == pytest.approx(
        (-122.6 + 3.5 * 0.00128, 45.5 + 4.5 * 0.0009), abs=1e-7
    )


def test_portland_bbox_keeps_the_whole_grid(tmp_path: Path) -> None:
    unclipped, _, unclipped_rows = run(tmp_path / "a", "grid")
    clipped, _, clipped_rows = run(tmp_path / "b", "grid", bbox=REGIONS["portland"].bbox)
    assert clipped == unclipped
    assert clipped_rows == unclipped_rows


def test_exclusions(tmp_path: Path) -> None:
    stats, roads, rows = run(tmp_path, "exclusions")
    nodes, ways, _ = contents(roads)
    assert ways == [200, 221, 222, 223, 224]
    assert nodes == list(range(1, 17)) + [113, 114, 115, 116]
    assert (stats.ways_read, stats.ways_excluded, stats.ways_kept) == (16, 11, 5)
    assert rows == []


def test_oneway_and_culdesac_keep_everything(tmp_path: Path) -> None:
    stats, roads, _ = run(tmp_path / "oneway", "oneway")
    assert contents(roads)[1] == list(range(300, 309))
    assert (stats.ways_read, stats.ways_kept) == (9, 9)
    stats, roads, rows = run(tmp_path / "culdesac", "culdesac")
    assert contents(roads)[:2] == ([1, 2, 3, 4, 5, 6, 7, 21, 22, 23], [400, 401, 402, 403])
    assert rows == []  # a turning circle is not a POI


def test_bbox_clip(tmp_path: Path) -> None:
    stats, roads, rows = run(tmp_path, "bbox", bbox=BBOX)
    nodes, ways, _ = contents(roads)
    assert ways == [500, 502]  # inside, and straddling; not outside, nor crossing with no node in
    assert nodes == [1, 2, 5, 6, 7]  # the straddling way whole
    assert (stats.ways_read, stats.ways_outside, stats.ways_kept) == (4, 2, 2)
    # The level crossing at node 6 lies outside the box but on a kept way, so it stays; the
    # traffic signals at node 11, outside and on no kept way, do not.
    assert sorted((r["osm_id"], r["kind"]) for r in rows) == [
        (6, "level_crossing"),
        (10, "toll_booth"),
    ]


def test_no_bbox_keeps_everything(tmp_path: Path) -> None:
    stats, roads, rows = run(tmp_path, "bbox")
    assert contents(roads)[1] == [500, 501, 502, 503]
    assert stats.ways_outside == 0
    assert sorted(r["osm_id"] for r in rows) == [6, 10, 11]


def test_sources_in_order_skip_what_is_taken(tmp_path: Path) -> None:
    once, roads_once, rows_once = run(tmp_path / "once", "grid")
    twice, roads_twice, rows_twice = run(tmp_path / "twice", "grid", "grid")
    assert contents(roads_twice) == contents(roads_once)
    assert (twice.ways_read, twice.ways_duplicate, twice.ways_kept) == (24, 12, 12)
    assert rows_twice == rows_once
    assert twice.pois == once.pois


def test_two_extracts_sharing_a_border_road_merge_into_one(tmp_path: Path) -> None:
    # Both extracts hold Border Hwy (900) complete, its nodes and the POIs by the line.
    stats, roads, rows = run(tmp_path, "border_south", "border_north")
    nodes, ways, tagged = contents(roads)
    assert ways == [900, 901, 902]
    assert nodes == [9001, 9002, 9003, 9004, 9010, 9020]  # each once, sorted
    assert tagged == {}
    refs = {w.id: [n.ref for n in w.nodes] for w in osmium.FileProcessor(roads, osmium.osm.WAY)}
    assert refs == {900: [9001, 9002, 9003, 9004], 901: [9010, 9001], 902: [9004, 9020]}
    assert (stats.ways_read, stats.ways_duplicate, stats.ways_kept) == (4, 1, 3)
    assert sorted((r["osm_id"], r["kind"]) for r in rows) == [
        (9002, "traffic_signals"),
        (9050, "brand"),
    ]
    _, reversed_roads, reversed_rows = run(tmp_path / "reversed", "border_north", "border_south")
    assert contents(reversed_roads) == contents(roads)
    assert sorted(r["osm_id"] for r in reversed_rows) == sorted(r["osm_id"] for r in rows)


def osm_text(
    nodes: dict[int, tuple[int, float, float, dict[str, str]]], ways: dict[int, list[int]]
) -> str:
    """An OSM file of `nodes` (id: version, lat, lon, tags) and two-way residential `ways`."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="hand">']
    for node, (version, lat, lon, tags) in sorted(nodes.items()):
        kvs = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
        lines.append(f'<node id="{node}" version="{version}" lat="{lat}" lon="{lon}">{kvs}</node>')
    for way, refs in sorted(ways.items()):
        nds = "".join(f'<nd ref="{r}"/>' for r in refs)
        lines.append(f'<way id="{way}" version="1">{nds}<tag k="highway" v="residential"/></way>')
    return "\n".join([*lines, "</osm>"]) + "\n"


def test_the_merge_keeps_each_node_s_newest_version_as_one_extract_would(tmp_path: Path) -> None:
    # Two extracts of different days share road 900 and its nodes; the later one moved node 3
    # (version 2). Merged, the roads hold version 2, and the merge's roads and POIs are the
    # bytes the same filter writes from one extract holding the union.
    signal = {"highway": "traffic_signals"}
    old_3, new_3 = (1, 45.5010000, -122.6000000, {}), (2, 45.5011000, -122.6001000, {})
    shared = {
        1: (1, 45.4970000, -122.6, {}),
        2: (1, 45.4990000, -122.6, signal),
        4: (1, 45.503, -122.6, {}),
    }
    south = {**shared, 3: old_3, 10: (1, 45.495, -122.6, {})}
    north = {**shared, 3: new_3, 20: (1, 45.505, -122.6, {})}
    road, below, above = {900: [1, 2, 3, 4]}, {901: [10, 1]}, {902: [4, 20]}
    files = {
        "south": osm_text(south, {**road, **below}),
        "north": osm_text(north, {**road, **above}),
        "union": osm_text({**south, **north}, {**road, **below, **above}),
    }
    for name, text in files.items():
        (tmp_path / f"{name}.osm").write_text(text)
    merged, union = tmp_path / "merged", tmp_path / "union"
    for out, sources in ((merged, ["south", "north"]), (union, ["union"])):
        out.mkdir()
        filter_roads(
            [tmp_path / f"{s}.osm" for s in sources],
            out / "r.osm.pbf",
            out / "p.parquet",
            out / "t.parquet",
            None,
        )
    versions = {
        n.id: (n.version, n.location.lat, n.location.lon)
        for n in osmium.FileProcessor(merged / "r.osm.pbf", osmium.osm.NODE)
    }
    assert versions[3] == (2, 45.5011, -122.6001)
    assert (merged / "r.osm.pbf").read_bytes() == (union / "r.osm.pbf").read_bytes()
    assert (merged / "p.parquet").read_bytes() == (union / "p.parquet").read_bytes()


def test_the_streaming_merge_writes_what_merge_input_reader_writes(tmp_path: Path) -> None:
    # Three sorted parts with interleaved ids, nodes in two or three of them, node 3 at three
    # versions (the newest in the middle part) and node 6 at the same version twice.
    def node(version: int, lat: float) -> tuple[int, float, float, dict[str, str]]:
        return version, lat, -122.6, {}

    parts = [
        ({1: node(1, 45.50), 3: node(2, 45.51), 5: node(1, 45.52)}, {900: [1, 3, 5]}),
        ({2: node(1, 45.53), 3: node(3, 45.54), 6: node(1, 45.55)}, {901: [2, 3, 6]}),
        ({3: node(1, 45.56), 4: node(1, 45.57), 6: node(1, 45.55)}, {902: [3, 4, 6], 903: [4, 6]}),
    ]
    files = []
    for k, (nodes, ways) in enumerate(parts):
        files.append(tmp_path / f"part{k}.osm")
        files[-1].write_text(osm_text(nodes, ways))
    streamed, held = tmp_path / "streamed.osm.pbf", tmp_path / "held.osm.pbf"
    merge_parts(files, streamed)
    reader = osmium.MergeInputReader()
    for file in files:
        reader.add_file(str(file))
    with osmium.SimpleWriter(held) as writer:
        reader.apply(writer, simplify=True)
    assert streamed.read_bytes() == held.read_bytes()
    versions = {n.id: n.version for n in osmium.FileProcessor(streamed, osmium.osm.NODE)}
    assert versions == {1: 1, 2: 1, 3: 3, 4: 1, 5: 1, 6: 1}
    assert [w.id for w in osmium.FileProcessor(streamed, osmium.osm.WAY)] == [900, 901, 902, 903]


def merged_both_ways(files: list[Path], tmp_path: Path) -> Path:
    """`merge_parts` of `files`, checked byte for byte against `MergeInputReader`'s merge."""
    streamed, held = tmp_path / "streamed.osm.pbf", tmp_path / "held.osm.pbf"
    merge_parts(files, streamed)
    reader = osmium.MergeInputReader()
    for file in files:
        reader.add_file(str(file))
    with osmium.SimpleWriter(held) as writer:
        reader.apply(writer, simplify=True)
    assert streamed.read_bytes() == held.read_bytes()
    return streamed


def test_a_node_and_a_way_of_one_id_are_both_merged(tmp_path: Path) -> None:
    # Node 900 tops the heap while way 900 is b's next object: passing over by id alone, not by
    # type and id, would drop the way.
    a, b = tmp_path / "a.osm", tmp_path / "b.osm"
    a.write_text(osm_text({1: (1, 45.5, -122.6, {}), 900: (1, 45.51, -122.6, {})}, {}))
    b.write_text(osm_text({1: (1, 45.5, -122.6, {})}, {900: [1]}))
    merged = merged_both_ways([a, b], tmp_path)
    objects = [(o.type_str(), o.id) for o in osmium.FileProcessor(merged)]
    assert objects == [("n", 1), ("n", 900), ("w", 900)]


def test_an_object_twice_in_one_part_is_written_once(tmp_path: Path) -> None:
    # No part the filter writes holds one, but the merge passes over any further copy, as
    # MergeInputReader keeps one.
    doubled = tmp_path / "doubled.osm"
    node = '<node id="5" version="1" lat="45.5" lon="-122.6"/>'
    doubled.write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><osm version="0.6">{node}{node}'
        '<node id="6" version="1" lat="45.6" lon="-122.6"/></osm>\n'
    )
    other = tmp_path / "other.osm"
    other.write_text(osm_text({5: (1, 45.5, -122.6, {})}, {}))
    merged = merged_both_ways([doubled, other], tmp_path)
    assert [n.id for n in osmium.FileProcessor(merged)] == [5, 6]


def test_an_object_held_across_another_whole_file_is_written_intact(tmp_path: Path) -> None:
    # The merge holds a's only node while it reads all of b, block after block, then writes it.
    a, b = tmp_path / "a.osm", tmp_path / "b.osm.pbf"
    a.write_text(osm_text({10_000_000: (1, 45.6, -122.7, {"name": "held"})}, {}))
    with osmium.SimpleWriter(b) as writer:
        for node in range(1, 50_001):
            writer.add_node(osmium.osm.mutable.Node(id=node, version=1, location=(-122.6, 45.5)))
    merged = merged_both_ways([a, b], tmp_path)
    last = None
    for node in osmium.FileProcessor(merged, osmium.osm.NODE):
        last = (node.id, node.location.lat, node.location.lon, dict(node.tags))
    assert last == (10_000_000, 45.6, -122.7, {"name": "held"})


def test_a_version_tie_goes_to_the_newer_timestamp(tmp_path: Path) -> None:
    def extract(stamp: str, lat: float) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?><osm version="0.6" generator="hand">'
            f'<node id="3" version="2" timestamp="{stamp}" lat="{lat}" lon="-122.6"/></osm>\n'
        )

    files = [tmp_path / "older.osm", tmp_path / "newer.osm", tmp_path / "older_too.osm"]
    files[0].write_text(extract("2026-10-28T21:21:02Z", 45.50))
    files[1].write_text(extract("2026-10-29T21:21:26Z", 45.51))
    files[2].write_text(extract("2026-10-28T21:21:02Z", 45.52))
    merged = merged_both_ways(files, tmp_path)
    kept = [(n.location.lat, n.timestamp.isoformat()) for n in osmium.FileProcessor(merged)]
    assert kept == [(45.51, "2026-10-29T21:21:26+00:00")]
    # with the versions and the timestamps tied, the earliest part's, as MergeInputReader keeps
    (tmp_path / "tied").mkdir()
    merged = merged_both_ways([files[0], files[2]], tmp_path / "tied")
    assert [n.location.lat for n in osmium.FileProcessor(merged)] == [45.50]


def test_the_ceiling_counts_only_the_ways_a_bbox_keeps(tmp_path: Path) -> None:
    # Kept and above the ceiling together are what the region keeps without one, with a bbox
    # too: a way outside the box is outside, whatever its class.
    t1 = REGIONS["us-t1"].road_classes
    for name, bbox, kept, above, outside in [
        ("far", (-100.0, 40.0, -99.0, 41.0), 0, 0, 9),
        ("near", (-123.0, 45.0, -122.0, 46.0), 6, 3, 0),
    ]:
        capped, _, _ = run(tmp_path / name / "capped", "diamond", bbox=bbox, classes=t1)
        uncapped, _, _ = run(tmp_path / name / "uncapped", "diamond", bbox=bbox)
        assert (capped.ways_kept, capped.ways_above_ceiling, capped.ways_outside) == (
            kept,
            above,
            outside,
        )
        assert capped.ways_kept + capped.ways_above_ceiling == uncapped.ways_kept


def test_a_class_ceiling_keeps_only_its_classes(tmp_path: Path) -> None:
    t1 = REGIONS["us-t1"].road_classes
    stats, roads, rows = run(tmp_path, "diamond", classes=t1)
    # I-5's carriageways and ramps; not SE Main St (secondary) or the two residential streets.
    assert sorted(contents(roads)[1]) == [150, 151, 152, 153, 160, 161]
    assert (stats.ways_read, stats.ways_kept, stats.ways_above_ceiling) == (9, 6, 3)
    assert stats.ways_excluded == 0
    stats, roads, rows = run(tmp_path / "border", "border_south", "border_north", classes=t1)
    nodes, ways, _ = contents(roads)
    assert ways == [900] and nodes == [9001, 9002, 9003, 9004]
    assert (stats.ways_kept, stats.ways_duplicate, stats.ways_above_ceiling) == (1, 1, 2)
    assert len(rows) == 2  # the POIs are the region's, whatever its roads


def test_empty_poi_table_keeps_its_schema(tmp_path: Path) -> None:
    run(tmp_path, "culdesac")
    assert pq.read_schema(tmp_path / "pois.parquet").equals(POI_SCHEMA)


def test_outputs_replace_earlier_ones(tmp_path: Path) -> None:
    run(tmp_path, "grid")
    stats, roads, rows = run(tmp_path, "culdesac")
    assert contents(roads)[1] == [400, 401, 402, 403]
    assert rows == []
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["pois.parquet", "restrictions.parquet", "roads.osm.pbf"]


@pytest.mark.parametrize(
    ("tags", "kinds"),
    [
        ({"barrier": "toll_booth"}, ["toll_booth"]),
        ({"barrier": "gate"}, []),
        ({"railway": "level_crossing"}, ["level_crossing"]),
        ({"railway": "crossing"}, []),
        ({"highway": "stop"}, ["stop"]),
        ({"highway": "traffic_signals"}, ["traffic_signals"]),
        ({"highway": "residential"}, []),
        ({"brand": "Waffle House"}, ["brand"]),
        ({"brand:wikidata": "Q1701206"}, ["brand"]),
        ({"highway": "traffic_signals", "brand": "Acme"}, ["traffic_signals", "brand"]),
        ({"name": "Waffle House"}, []),
    ],
)
def test_poi_kinds(tags: dict[str, str], kinds: list[str]) -> None:
    assert poi_kinds(tags) == kinds


def test_source_timestamp(tmp_path: Path) -> None:
    assert source_timestamp(FIXTURES / "grid.osm") is None
    header = osmium.io.Header()
    header.set("osmosis_replication_timestamp", "2026-10-28T21:21:02Z")
    stamped = tmp_path / "stamped.osm.pbf"
    with osmium.SimpleWriter(stamped, header=header) as writer:
        writer.add_node(osmium.osm.mutable.Node(id=1, location=(-122.6, 45.5)))
    assert source_timestamp(stamped) == "2026-10-28T21:21:02Z"
