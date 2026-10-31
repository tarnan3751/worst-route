"""The arrays engine's enrich against the networkx engine's: the same values on every edge and
node, the same brand index and report, on every fixture, the stage B cases, the review's random
roads and a road mapped more than once (`copies.py`), each with POIs of every kind on every OSM
node of its roads (at junctions, inside edges, and ids on no road) and brands around them; and
the stage written and read back."""

import dataclasses
import xml.etree.ElementTree as ET
from pathlib import Path

import networkx as nx
import numpy as np
import pyarrow as pa
import pytest

from tests.pipeline.chain import FIXTURES, UTM_10N, brand_pois, pois_of
from tests.pipeline.copies import COPIES, OVERLAPS, mapped_copies, overlapping
from tests.pipeline.osm import write_osm
from tests.pipeline.parity import read_back
from tests.pipeline.random_roads import random_roads
from tests.pipeline.test_arrays import STAGE_B_CASES
from worst_route.pipeline.arrays import cleaned
from worst_route.pipeline.arrays import enrich as arrays_enrich
from worst_route.pipeline.arrays.build import build_raw_arrays
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.cleanup import run_arrays_cleanup, run_cleanup
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.filter import POI_SCHEMA

KINDS = ("toll_booth", "level_crossing", "stop", "traffic_signals", "brand")
BRANDS = [("Starbucks", "Q37158", None), ("starbucks ", None, "Coffee"), (None, None, "Fred's")]


def every_poi(source: Path) -> pa.Table:
    """POIs on every node of the file, one kind each in turn, ids on no road, and brands."""
    nodes = [
        (int(n.get("id", 0)), float(n.get("lon", 0)), float(n.get("lat", 0)))
        for n in ET.parse(source).getroot().iter("node")
    ]
    rows = []
    for k, (osm_id, lon, lat) in enumerate(nodes):
        kind = KINDS[k % len(KINDS)]
        brand, wikidata, name = BRANDS[k % len(BRANDS)] if kind == "brand" else (None,) * 3
        rows.append(("n", osm_id, lon + 1e-5, lat, kind, brand, wikidata, name))
        if k % 4 == 0:  # a second POI on the node, and one on no road
            rows.append(("n", osm_id, lon, lat, KINDS[(k + 1) % 4], None, None, None))
            rows.append(("n", 10**9 + osm_id, lon, lat, KINDS[k % 4], None, None, None))
        if k % 7 == 0:  # a way's POI: only a brand counts
            rows.append(("w", osm_id, lon, lat - 1e-5, "brand", "Shell", "Q154950", None))
    columns = {name: [row[i] for row in rows] for i, name in enumerate(POI_SCHEMA.names)}
    return pa.table(columns, schema=POI_SCHEMA)


def assert_same_enrich(source: Path, pois: pa.Table) -> None:
    want, _ = run_cleanup(read_back(build_raw_graph(source, UTM_10N)[0]), allow_fragmented=True)
    graph, _ = run_arrays_cleanup(build_raw_arrays(source, UTM_10N)[0], allow_fragmented=True)
    want_report = run_enrich(want, pois)
    got, got_report = arrays_enrich.run_enrich(graph, pois)
    assert dataclasses.replace(got_report, seconds=0.0) == dataclasses.replace(
        want_report, seconds=0.0
    )
    assert_same_values(want, got)


def assert_same_values(want: nx.MultiDiGraph, got: arrays_enrich.EnrichedGraph) -> None:
    keys = sorted(want.edges(keys=True))
    edge_id = {key: i for i, key in enumerate(keys)}
    edges = [want.edges[key] for key in keys]
    assert got.edge_speed_kmh.tolist() == [d["speed_kmh"] for d in edges]
    assert got.edge_speed_parsed.tolist() == [d["speed_parsed"] for d in edges]
    assert got.edge_time_s.tolist() == [d["time_s"] for d in edges]
    identities = [got.identities[i] if i >= 0 else None for i in got.edge_identity.tolist()]
    assert identities == [d["identity"] for d in edges]
    assert got.identities == sorted({d["identity"] for d in edges if d["identity"] is not None})
    assert got.edge_unit.tolist() == [edge_id[d["unit"]] for d in edges]
    for name in ("flags", "toll_booths", "level_crossings", "bearing_in", "bearing_out"):
        assert getattr(got, f"edge_{name}").tolist() == [d[name] for d in edges], name
    nodes = [want.nodes[v] for v in range(want.number_of_nodes())]
    assert got.node_delay_s.tolist() == [d["delay_s"] for d in nodes]
    assert got.node_pass_through.tolist() == [d["pass_through"] for d in nodes]
    assert got.brands == want.graph["brands"]


CASES = {
    **{f"fixture {path.stem}": (lambda tmp, p=path: p) for path in FIXTURES.glob("*.osm")},
    **{f"stage B {name}": make for name, make in STAGE_B_CASES.items()},
    **{
        f"random {seed}": (lambda tmp, s=seed: write_osm(tmp / f"random-{s}.osm", *random_roads(s)))
        for seed in (*range(10), 13, 26, 48)
    },
    **{
        f"copies: {name}{', an inner junction' if inner else ''}": (
            lambda tmp, c=copies, i=inner: mapped_copies(tmp / "copies.osm", c, inner=i)
        )
        for name, copies in COPIES.items()
        for inner in (False, True)
    },
    **{
        f"overlap: {name}": (lambda tmp, w=ways: overlapping(tmp / "overlap.osm", w))
        for name, (ways, _) in OVERLAPS.items()
    },
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_enrich_is_the_same(tmp_path: Path, case: str) -> None:
    source = CASES[case](tmp_path)
    assert_same_enrich(source, every_poi(source))


@pytest.mark.parametrize("name", ["bbox", "grid"])
def test_enrich_with_the_filters_pois(tmp_path: Path, name: str) -> None:
    source = FIXTURES / f"{name}.osm"
    assert_same_enrich(source, pois_of(source, tmp_path))


def test_the_stage_reads_back(tmp_path: Path) -> None:
    source = FIXTURES / "grid.osm"
    graph, report = run_arrays_cleanup(build_raw_arrays(source, UTM_10N)[0], allow_fragmented=True)
    cleaned.save_stage(graph, report, tmp_path)
    graph = cleaned.load_stage(tmp_path)
    enriched, enrich_report = arrays_enrich.run_enrich(graph, every_poi(source))
    arrays_enrich.save_stage(enriched, enrich_report, tmp_path)
    back = arrays_enrich.load_stage(cleaned.load_stage(tmp_path), tmp_path)
    for field in dataclasses.fields(enriched):
        if field.name != "graph":
            a, b = getattr(enriched, field.name), getattr(back, field.name)
            same = np.array_equal(a, b) if isinstance(a, np.ndarray) else a == b
            assert same, field.name


def test_a_brand_between_two_nodes_snaps_to_the_lower() -> None:
    """A brand POI halfway between two pass-through nodes snaps to the lower of the two, on both
    engines (`enrich.index_brands`' tie rule): the grid's nodes placed on a line of whole degrees
    in EPSG:4326, so that halfway is exactly as far from each, and three POIs between 0 and 1,
    2 and 3, 4 and 5."""
    source = FIXTURES / "grid.osm"
    want, _ = run_cleanup(read_back(build_raw_graph(source, UTM_10N)[0]))
    want.graph["crs"] = "EPSG:4326"
    for v in want.nodes:
        want.nodes[v]["x"], want.nodes[v]["y"] = float(v), 0.0
    graph, _ = run_arrays_cleanup(build_raw_arrays(source, UTM_10N)[0])
    n = len(graph.node_x)
    graph = dataclasses.replace(
        graph,
        node_x=np.arange(n, dtype=np.float64),
        node_y=np.zeros(n),
        stage_a=dataclasses.replace(graph.stage_a, crs_epsg=4326),
    )
    pois = brand_pois([(k + 0.5, 0.0, "Fred's", None, None) for k in (0, 2, 4)])
    run_enrich(want, pois)
    got, _ = arrays_enrich.run_enrich(graph, pois)
    assert want.graph["brands"] == got.brands == [("Fred's", None, [(0, 1), (2, 1), (4, 1)])]
