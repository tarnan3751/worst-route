"""`pipeline save`: the enriched graph and its tiers, written as a build (`docs/PLAN.md` §2.7).

`docs/reference/pipeline.md`, save; the format is `docs/reference/graph.md`. `graph_data(graph,
tier_nodes, meta)` turns the enriched graph into `GraphData`:

- **Edge ids** are the positions of the edge keys `(u, v, key)` in sorted order, the format's
  (u, v, original order): each node's out-edges are one run of ascending ids, and its in-edges
  are listed in ascending id. A unit's id is the id of its key (`enrich`: the smaller of the
  two directions of a road).
- **Nodes**: position, `kind` as `NodeKind`, `node_state` −1 (a route's states come from the
  Census polygons at presentation time, `presentation/places.py`, so it stays −1),
  `pass_through`, `delay_s`, and `members`.
- **Edges**: the enriched attributes; `edge_highway` as `HighwayClass`; `edge_tier` the first
  tier whose classes hold the class; `edge_name` and `edge_ref` index the raw tags and
  `edge_identity` the identities, each table sorted; `way_osm_ids` from `osmids`,
  `osmnode_ids` from `osm_nodes`, and `coords` the projected geometry turned back into
  (lon, lat), float32.
- **Twins** and **transitions**: the cleaned graph's tables with keys turned into ids, rows
  sorted.
- **Tiers**: `tier_node_mask[t]` the nodes `tiers` kept; `tier_edge_mask[t]` the edges of the
  tier's classes with both ends kept.
- **Collectibles**: `node_roundabouts`, the roundabout rings each super-junction's record counts
  (an interchange that holds a ring counts it too), 0 at every other node.
- **Brands**: enrich's index (`enrich.index_brands`) as `brand_indptr`, `brand_nodes` and
  `brand_locations`, in its order (the brand ids), each brand's name and wikidata id in
  `strings.brands` and `strings.brand_wikidata`.

It refuses a super-node whose transition table is empty (`EmptyTransitionsError`): for cleanup
that means no turn through it is allowed, but `GraphData` reads a node without rows as allowing
every turn, so the build cannot say it.

`build_meta` assembles the build's `meta`: the region, the time, `counts` (filled in by
`graph_data`), `params` (`build_params`: the region, its bbox and CRS and the configuration the
cleanup, enrich and tiers sidecars recorded, without the pipeline version, which `meta` keeps on
its own) and `sources` (`build_sources`: the extract `filter` read, its md5 and data date,
refused with `SourcesChangedError` when the filtered roads are no longer those `build` read).
`save_build(data, build_dir)` writes it with `graph.store.write_build` beside `stages/`, reads
it back with `read_build` and raises `RuntimeError` if anything differs, then writes
`stages/save.json` (the counts and `graph_version`). `remove_build` deletes a written build
(`meta.json` first, so no reader sees half of one) for `--force`.
"""

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pyproj
import shapely
from numpy.typing import NDArray

from worst_route.config import TIERS, HighwayClass, Region
from worst_route.graph.data import ARRAY_SPECS, GraphData, NodeKind, Strings
from worst_route.graph.store import read_build, write_build
from worst_route.pipeline import PIPELINE_VERSION, layout

type EdgeKey = tuple[int, int, int]


# The steps whose sidecar's configuration goes into the build's params, with their outputs.
STEP_OUTPUTS = (("cleanup", "cleanup.pkl"), ("enrich", "enrich.pkl"), ("tiers", "tiers.json"))


class EmptyTransitionsError(ValueError):
    """A super-node allows no turn, which the build format cannot express."""


class SourcesChangedError(ValueError):
    """The filtered extract is not the one the build was made from."""


@dataclass
class SaveReport:
    """What save wrote."""

    graph_version: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    megabytes: float = 0.0  # arrays/ and strings.json
    seconds: float = 0.0


def graph_data(
    graph: nx.MultiDiGraph, tier_nodes: list[list[int]], meta: dict[str, Any]
) -> GraphData:
    """The enriched graph as `GraphData`, with `meta` and its counts; `graph_version` empty."""
    n = graph.number_of_nodes()
    keys: list[EdgeKey] = sorted(graph.edges(keys=True))
    edge_id = {key: i for i, key in enumerate(keys)}
    edges = [graph.edges[key] for key in keys]
    nodes = [graph.nodes[v] for v in range(n)]  # cleanup numbers the nodes 0..n-1
    records: dict[int, dict[str, Any]] = graph.graph["superjunctions"]
    for node, record in records.items():
        if not record["transitions"]:
            raise EmptyTransitionsError(
                f"super-junction {node} ({record['kind']}, at {nodes[node]['lat']:.5f}, "
                f"{nodes[node]['lon']:.5f}) allows no turn through it, which a build cannot "
                "express: a node without transition rows allows every turn"
            )

    brands: list[tuple[str, str | None, list[tuple[int, int]]]] = graph.graph["brands"]
    strings = Strings(
        names=sorted({d["name"] for d in edges if "name" in d}),
        refs=sorted({d["ref"] for d in edges if "ref" in d}),
        identities=sorted({d["identity"] for d in edges if d["identity"] is not None}),
        brands=[name for name, _, _ in brands],
        brand_wikidata=[wikidata for _, wikidata, _ in brands],
    )
    index = {
        table: {value: i for i, value in enumerate(getattr(strings, table))}
        for table in ("names", "refs", "identities")
    }
    highway = np.array([HighwayClass[d["highway"].upper()] for d in edges], dtype=np.uint8)
    edge_u = np.array([u for u, _, _ in keys], dtype=np.int32)
    edge_v = np.array([v for _, v, _ in keys], dtype=np.int32)

    geometries = np.asarray([d["geometry"] for d in edges], dtype=object)
    xy = shapely.get_coordinates(geometries)
    to_lonlat = pyproj.Transformer.from_crs(graph.graph["crs"], 4326, always_xy=True)
    lon, lat = to_lonlat.transform(xy[:, 0], xy[:, 1])

    twins: dict[EdgeKey, tuple[EdgeKey, ...]] = graph.graph["twins"]
    twin_rows = [sorted(edge_id[t] for t in twins.get(key, ())) for key in keys]
    trans = np.array(
        sorted(
            (node, edge_id[into], edge_id[out])
            for node, record in records.items()
            for into, out in record["transitions"]
        ),
        dtype=np.int32,
    ).reshape(-1, 3)

    tier_node_mask = np.zeros((len(TIERS), n), dtype=np.bool_)
    tier_edge_mask = np.zeros((len(TIERS), len(keys)), dtype=np.bool_)
    for t, classes in enumerate(TIERS):
        tier_node_mask[t, tier_nodes[t]] = True
        in_tier = np.isin(highway, np.array(sorted(classes), dtype=np.uint8))
        tier_edge_mask[t] = in_tier & tier_node_mask[t, edge_u] & tier_node_mask[t, edge_v]
    tier_of = {c: next(t for t, classes in enumerate(TIERS) if c in classes) for c in HighwayClass}

    counts = {
        "nodes": n,
        "edges": len(keys),
        "twin_pairs": sum(map(len, twin_rows)) // 2,
        "transitions": len(trans),
        "superjunctions": len(records),
    }
    data = GraphData(
        node_lon=np.array([d["lon"] for d in nodes], dtype=np.float64),
        node_lat=np.array([d["lat"] for d in nodes], dtype=np.float64),
        node_x=np.array([d["x"] for d in nodes], dtype=np.float64),
        node_y=np.array([d["y"] for d in nodes], dtype=np.float64),
        node_kind=np.array([NodeKind[d["kind"].upper()] for d in nodes], dtype=np.uint8),
        node_state=np.full(n, -1, dtype=np.int16),
        node_pass_through=np.array([d["pass_through"] for d in nodes], dtype=np.bool_),
        node_delay_s=np.array([d["delay_s"] for d in nodes], dtype=np.float32),
        member_indptr=_indptr([len(d["members"]) for d in nodes]),
        member_osm_ids=_concat([d["members"] for d in nodes]),
        edge_u=edge_u,
        edge_v=edge_v,
        edge_length_m=np.array([d["length"] for d in edges], dtype=np.float32),
        edge_speed_kmh=np.array([d["speed_kmh"] for d in edges], dtype=np.float32),
        edge_time_s=np.array([d["time_s"] for d in edges], dtype=np.float32),
        edge_highway=highway,
        edge_tier=np.array([tier_of[c] for c in highway.tolist()], dtype=np.uint8),
        edge_name=_lookup(index["names"], [d.get("name") for d in edges]),
        edge_ref=_lookup(index["refs"], [d.get("ref") for d in edges]),
        edge_identity=_lookup(index["identities"], [d["identity"] for d in edges]),
        edge_unit=np.array([edge_id[d["unit"]] for d in edges], dtype=np.int32),
        edge_flags=np.array([d["flags"] for d in edges], dtype=np.uint8),
        edge_toll_booths=np.array([d["toll_booths"] for d in edges], dtype=np.uint8),
        edge_level_crossings=np.array([d["level_crossings"] for d in edges], dtype=np.uint8),
        edge_bearing_in=np.array([d["bearing_in"] for d in edges], dtype=np.int16),
        edge_bearing_out=np.array([d["bearing_out"] for d in edges], dtype=np.int16),
        way_indptr=_indptr([len(d["osmids"]) for d in edges]),
        way_osm_ids=_concat([d["osmids"] for d in edges]),
        out_indptr=_indptr(np.bincount(edge_u, minlength=n).tolist()),
        out_edges=np.arange(len(keys), dtype=np.int32),
        in_indptr=_indptr(np.bincount(edge_v, minlength=n).tolist()),
        in_edges=np.argsort(edge_v, kind="stable").astype(np.int32),
        coord_indptr=_indptr(shapely.get_num_coordinates(geometries).tolist()),
        coords=np.column_stack([lon, lat]).astype(np.float32),
        osmnode_indptr=_indptr([len(d["osm_nodes"]) for d in edges]),
        osmnode_ids=_concat([d["osm_nodes"] for d in edges]),
        twin_indptr=_indptr([len(row) for row in twin_rows]),
        twin_edges=np.array([e for row in twin_rows for e in row], dtype=np.int32),
        trans_node=trans[:, 0].copy(),
        trans_in=trans[:, 1].copy(),
        trans_out=trans[:, 2].copy(),
        tier_node_mask=tier_node_mask,
        tier_edge_mask=tier_edge_mask,
        node_roundabouts=np.array(
            [records[v]["roundabouts"] if v in records else 0 for v in range(n)], dtype=np.uint8
        ),
        brand_indptr=_indptr([len(at) for _, _, at in brands]),
        brand_nodes=np.array([v for _, _, at in brands for v, _ in at], dtype=np.int32),
        brand_locations=np.array([k for _, _, at in brands for _, k in at], dtype=np.int32),
        strings=strings,
        meta={**meta, "counts": counts, "graph_version": ""},
    )
    for name in ARRAY_SPECS:
        getattr(data, name).flags.writeable = False
    return data


def _indptr(lengths: list[int]) -> NDArray[np.int64]:
    return np.concatenate(([0], np.cumsum(lengths, dtype=np.int64))).astype(np.int64)


def _concat(runs: list[Any]) -> NDArray[np.int64]:
    return np.array([x for run in runs for x in run], dtype=np.int64)


def _lookup(index: dict[str, int], values: list[str | None]) -> NDArray[np.int32]:
    return np.array([-1 if v is None else index[v] for v in values], dtype=np.int32)


def build_params(region: Region, build_dir: Path) -> dict[str, Any]:
    """The build's params: the region, its bbox and CRS, and each step's recorded configuration.

    The configuration is what the cleanup, enrich and tiers sidecars recorded (their
    `allow_fragmented` and `config`), without the pipeline version, which `meta` keeps apart.
    """
    params: dict[str, Any] = {
        "region": region.name,
        "bbox": None if region.bbox is None else list(region.bbox),
        "crs_epsg": region.crs_epsg,
    }
    for step, output in STEP_OUTPUTS:
        sidecar = layout.sidecar(layout.stage_file(build_dir, output))
        recorded = json.loads(sidecar.read_text())["params"]
        params[step] = {k: v for k, v in recorded.items() if k != "pipeline_version"}
    return params


def build_sources(region: Region, build_dir: Path) -> list[dict[str, Any]]:
    """The extract `filter` read for this build: file, md5 and data date.

    Raises `SourcesChangedError` when the filtered roads are no longer the ones `build` read
    (their md5 against the one `build.pkl`'s sidecar recorded): the filter's record then
    describes another build's extract.
    """
    roads = layout.roads_pbf(region.name)
    built = json.loads(layout.sidecar(layout.stage_file(build_dir, "build.pkl")).read_text())
    if [source["md5"] for source in built["inputs"]] != [layout.file_md5(roads)]:
        raise SourcesChangedError(
            "the filtered extract changed since this build was built; rebuild"
        )
    filtered = json.loads(layout.sidecar(roads).read_text())
    return [
        {**source, "source_timestamp": filtered["source_timestamp"]}
        for source in filtered["inputs"]
    ]


def build_meta(
    region: Region, build_id: str, params: dict[str, Any], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    """The build's meta, but for its counts and graph_version."""
    return {
        "region": region.name,
        "build_id": build_id,
        "crs_epsg": region.crs_epsg,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "pipeline_version": PIPELINE_VERSION,
        "params": params,
        "sources": sources,
    }


def save_build(data: GraphData, build_dir: Path) -> SaveReport:
    """Write the build into `build_dir`, check it reads back the same, write `save.json`."""
    clock = time.perf_counter()
    version = write_build(data, build_dir)
    loaded = read_build(build_dir, mmap=False)
    for name in ARRAY_SPECS:
        written, read = getattr(data, name), getattr(loaded, name)
        if written.dtype != read.dtype or not np.array_equal(written, read):
            raise RuntimeError(f"{build_dir}: {name} reads back different from what was written")
    if loaded.strings != data.strings or loaded.meta != {**data.meta, "graph_version": version}:
        raise RuntimeError(f"{build_dir}: strings or meta read back different")
    size = sum(p.stat().st_size for p in (build_dir / "arrays").iterdir())
    size += (build_dir / "strings.json").stat().st_size
    report = SaveReport(
        graph_version=version,
        counts=dict(data.meta["counts"]),
        megabytes=round(size / 1e6, 1),
        seconds=round(time.perf_counter() - clock, 3),
    )
    layout.stage_file(build_dir, "save.json").write_text(
        json.dumps(asdict(report), indent=2) + "\n"
    )
    return report


def remove_build(build_dir: Path) -> None:
    """Delete a written build, `meta.json` first; the stages stay."""
    (build_dir / "meta.json").unlink(missing_ok=True)
    if (build_dir / "arrays").exists():
        shutil.rmtree(build_dir / "arrays")
    (build_dir / "strings.json").unlink(missing_ok=True)
