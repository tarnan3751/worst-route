from pathlib import Path

import osmium
import pyarrow.parquet as pq
import pytest

from worst_route.config import REGIONS
from worst_route.pipeline.filter import (
    POI_SCHEMA,
    FilterStats,
    filter_roads,
    poi_kinds,
    source_timestamp,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
BBOX = (-122.605, 45.495, -122.595, 45.505)  # the box bbox.osm is drawn around
GRID_STREETS = list(range(2000, 2006)) + list(range(2100, 2106))
GRID_ROAD_NODES = [1000 + 10 * r + c for r in range(6) for c in range(6)] + [1100, 1101, 1102, 1103]


def run(tmp_path: Path, *names: str, bbox: tuple[float, float, float, float] | None = None):
    roads, pois = tmp_path / "roads.osm.pbf", tmp_path / "pois.parquet"
    stats = filter_roads([FIXTURES / f"{n}.osm" for n in names], roads, pois, bbox)
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
            [tmp_path / f"{s}.osm" for s in sources], out / "r.osm.pbf", out / "p.parquet", None
        )
    versions = {
        n.id: (n.version, n.location.lat, n.location.lon)
        for n in osmium.FileProcessor(merged / "r.osm.pbf", osmium.osm.NODE)
    }
    assert versions[3] == (2, 45.5011, -122.6001)
    assert (merged / "r.osm.pbf").read_bytes() == (union / "r.osm.pbf").read_bytes()
    assert (merged / "p.parquet").read_bytes() == (union / "p.parquet").read_bytes()


def test_empty_poi_table_keeps_its_schema(tmp_path: Path) -> None:
    run(tmp_path, "culdesac")
    assert pq.read_schema(tmp_path / "pois.parquet").equals(POI_SCHEMA)


def test_outputs_replace_earlier_ones(tmp_path: Path) -> None:
    run(tmp_path, "grid")
    stats, roads, rows = run(tmp_path, "culdesac")
    assert contents(roads)[1] == [400, 401, 402, 403]
    assert rows == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pois.parquet", "roads.osm.pbf"]


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
