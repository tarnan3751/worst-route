"""The pipeline's steps run in memory on a fixture, for the tests of the later steps."""

from pathlib import Path
from typing import Any

import networkx as nx
import pyarrow as pa
import pyarrow.parquet as pq
import pyproj

from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.cleanup import run_cleanup
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.filter import POI_SCHEMA, filter_roads
from worst_route.pipeline.tiers import run_tiers

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
UTM_10N = 32610
TO_LONLAT = pyproj.Transformer.from_crs(UTM_10N, 4326, always_xy=True)


def cleaned(source: Path, *, allow_fragmented: bool = False) -> nx.MultiDiGraph:
    """The cleanup stage of an OSM file."""
    raw = build_raw_graph(source, UTM_10N)[0]
    return run_cleanup(raw, allow_fragmented=allow_fragmented)[0]


def pois_of(source: Path, tmp: Path) -> pa.Table:
    """The POI table `filter` writes for an OSM file."""
    filter_roads([source], tmp / f"{source.stem}.roads.pbf", tmp / f"{source.stem}.pois", None)
    return pq.read_table(tmp / f"{source.stem}.pois")


def poi_table(rows: list[tuple[str, int, str]]) -> pa.Table:
    """A POI table from (osm_type, osm_id, kind) rows, at (0, 0), no brand or name."""
    columns: dict[str, list[object]] = {name: [] for name in POI_SCHEMA.names}
    for osm_type, osm_id, kind in rows:
        values = (osm_type, osm_id, 0.0, 0.0, kind, None, None, None)
        for name, value in zip(POI_SCHEMA.names, values, strict=True):
            columns[name].append(value)
    return pa.table(columns, schema=POI_SCHEMA)


def brand_pois(rows: list[tuple[float, float, Any, Any, Any]]) -> pa.Table:
    """A POI table of brand rows `(lon, lat, brand, wikidata, name)`; every third a way."""
    columns: dict[str, list[object]] = {name: [] for name in POI_SCHEMA.names}
    for k, (lon, lat, brand, wikidata, name) in enumerate(rows):
        values = ("w" if k % 3 == 2 else "n", 1000 + k, lon, lat, "brand", brand, wikidata, name)
        for column, value in zip(POI_SCHEMA.names, values, strict=True):
            columns[column].append(value)
    return pa.table(columns, schema=POI_SCHEMA)


def east_of(graph: nx.MultiDiGraph, node: int, dx: float) -> tuple[float, float]:
    """(lon, lat) `dx` metres east of a node (west when negative)."""
    lon, lat = TO_LONLAT.transform(graph.nodes[node]["x"] + dx, graph.nodes[node]["y"])
    return float(lon), float(lat)


def node_of(graph: nx.MultiDiGraph, osm_id: int) -> int:
    """The node whose members hold the OSM node id."""
    (node,) = (n for n, members in graph.nodes(data="members") if osm_id in members)
    return int(node)


def enriched(source: Path, tmp: Path, *, allow_fragmented: bool = False) -> nx.MultiDiGraph:
    """The enrich stage of an OSM file, with the POIs `filter` finds in it."""
    graph = cleaned(source, allow_fragmented=allow_fragmented)
    run_enrich(graph, pois_of(source, tmp))
    return graph


def tier_nodes(graph: nx.MultiDiGraph) -> list[list[int]]:
    """Each tier's kept nodes, fragmented or not."""
    return [t.node_ids for t in run_tiers(graph, allow_fragmented=True).tiers]
