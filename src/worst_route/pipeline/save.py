"""`pipeline save`: the enriched graph and its tiers, written as a build (`docs/PLAN.md` §2.7).

`docs/reference/pipeline.md`, save; the format is `docs/reference/graph.md`. `graph_data(graph,
tier_nodes, meta)` turns the enriched graph into `GraphData`:

- **Edge ids** are the positions of the edge keys `(u, v, key)` in sorted order, the format's
  (u, v, original order): each node's out-edges are one run of ascending ids, and its in-edges
  are listed in ascending id. A unit's id is the id of its key (`enrich`: the smaller of the
  two directions of a road).
- **Nodes**: position (projected x, y; the build derives lon/lat from them), `kind` as
  `NodeKind`, `pass_through`, `delay_s`, and `members`.
- **Edges**: the enriched attributes; `edge_highway` as `HighwayClass`; `edge_tier` the first
  tier whose classes hold the class; `edge_name` and `edge_ref` index the raw tags and
  `edge_identity` the identities, each table sorted; `speed_kmh` the distinct speeds, ascending,
  as float32, and `edge_speed` each edge's index among them (more than `MAX_SPEEDS` distinct
  speeds raise `TooManySpeedsError`). The OSM nodes along each edge (`osm_nodes`) and its way
  ids (`osmids`) stay in the stages.
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
every turn, so the build cannot say it. It refuses too a graph that breaks what the validator's
proof from original OSM ids rests on, now that the build keeps only the nodes' members
(`ShapePointError`, `docs/reference/engine.md`, the validator's `osm_ids`): an edge that does
not start and end at OSM nodes merged into its tail and head, or an OSM node inside an edge
that lies inside it twice, is some node's member, or lies inside two road units.

`build_meta` assembles the build's `meta`: the region, the time, `counts` (filled in by
`graph_data`), `params` (`build_params`: the region, its bbox and CRS, the configuration the
cleanup, enrich and tiers sidecars recorded, and save's own, `parameters()`: the geometry's
simplification tolerance; without the pipeline version, which `meta` keeps on
its own) and `sources` (`build_sources`: the extract `filter` read, its md5 and data date,
refused with `SourcesChangedError` when the filtered roads are no longer those `build` read).
`graph_geometry(graph, data)` is the build's geometry (`graph/geometry.py`): each edge's
projected shape simplified by Douglas–Peucker to `GEOMETRY_SIMPLIFY_M` (shapely, its ends kept;
the lengths and bearings were measured on the whole shape at enrich) and encoded as whole-metre
steps. `save_build(data, build_dir, geometry)` writes both with `graph.store.write_build` beside
`stages/`, reads them back with `read_build` and `read_geometry` and raises `RuntimeError` if
anything differs, then writes `stages/save.json` (the counts, `graph_version`, the megabytes of
the arrays and strings the engine reads and of the geometry). `remove_build` deletes a written build
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
import shapely
from numpy.typing import NDArray

from worst_route.config import GEOMETRY_SIMPLIFY_M, TIERS, HighwayClass, Region
from worst_route.graph.data import (
    ARRAY_SPECS,
    FORMAT_VERSION,
    MAX_SPEEDS,
    GraphData,
    NodeKind,
    Strings,
)
from worst_route.graph.geometry import GEOMETRY_SPECS, Geometry, encode, read_geometry
from worst_route.graph.store import read_build, write_build
from worst_route.pipeline import PIPELINE_VERSION, layout

type EdgeKey = tuple[int, int, int]


# The steps whose sidecar's configuration goes into the build's params, with their outputs.
STEP_OUTPUTS = (("cleanup", "cleanup.pkl"), ("enrich", "enrich.pkl"), ("tiers", "tiers.json"))


class EmptyTransitionsError(ValueError):
    """A super-node allows no turn, which the build format cannot express."""


class ShapePointError(ValueError):
    """An edge's OSM nodes break the validator's proof: its ends not its nodes' members, or an
    OSM node inside it that is a member or lies inside another road unit."""


class TooManySpeedsError(ValueError):
    """More distinct speeds than the build's uint8 `edge_speed` can index."""


class SourcesChangedError(ValueError):
    """The filtered extract is not the one the build was made from."""


@dataclass
class SaveReport:
    """What save wrote."""

    graph_version: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    megabytes: float = 0.0  # arrays/ and strings.json, what the engine reads
    geometry_megabytes: float = 0.0  # geometry/
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

    _check_shape_points(graph, keys)

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
    speeds = np.array([d["speed_kmh"] for d in edges], dtype=np.float32)
    speed_kmh = np.unique(speeds)
    if len(speed_kmh) > MAX_SPEEDS:
        raise TooManySpeedsError(
            f"{len(speed_kmh)} distinct speeds, more than the {MAX_SPEEDS} a build can index"
        )
    edge_u = np.array([u for u, _, _ in keys], dtype=np.int32)
    edge_v = np.array([v for _, v, _ in keys], dtype=np.int32)

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
        node_x=np.array([d["x"] for d in nodes], dtype=np.float64),
        node_y=np.array([d["y"] for d in nodes], dtype=np.float64),
        node_kind=np.array([NodeKind[d["kind"].upper()] for d in nodes], dtype=np.uint8),
        node_pass_through=np.array([d["pass_through"] for d in nodes], dtype=np.bool_),
        node_delay_s=np.array([d["delay_s"] for d in nodes], dtype=np.float32),
        member_indptr=_indptr([len(d["members"]) for d in nodes]),
        member_osm_ids=_concat([d["members"] for d in nodes]),
        edge_u=edge_u,
        edge_v=edge_v,
        edge_length_m=np.array([d["length"] for d in edges], dtype=np.float32),
        edge_speed=np.searchsorted(speed_kmh, speeds).astype(np.uint8),
        speed_kmh=speed_kmh,
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
        out_indptr=_indptr(np.bincount(edge_u, minlength=n).tolist()),
        out_edges=np.arange(len(keys), dtype=np.int32),
        in_indptr=_indptr(np.bincount(edge_v, minlength=n).tolist()),
        in_edges=np.argsort(edge_v, kind="stable").astype(np.int32),
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


def _check_shape_points(graph: nx.MultiDiGraph, keys: list[EdgeKey]) -> None:
    """Raise `ShapePointError` unless each edge starts and ends at OSM nodes merged into its
    tail and head, and each OSM node inside an edge lies inside it once, is no node's member and
    lies inside one road unit (an edge, or the two directions of a road) only."""
    member_of = {m: v for v, members in graph.nodes(data="members") for m in members}
    unit_of: dict[int, EdgeKey] = {}
    for key in keys:
        u, v, _ = key
        data = graph.edges[key]
        ids = data["osm_nodes"]
        if member_of.get(ids[0]) != u or member_of.get(ids[-1]) != v:
            raise ShapePointError(
                f"edge {key} does not start and end at OSM nodes merged into nodes {u} and {v}"
            )
        if len(set(ids)) != len(ids):
            twice = next(i for k, i in enumerate(ids) if i in ids[:k])
            raise ShapePointError(f"OSM node {twice} lies twice inside edge {key}")
        for osm_id in ids[1:-1]:
            if osm_id in member_of:
                raise ShapePointError(
                    f"OSM node {osm_id} lies inside edge {key} and is merged into node "
                    f"{member_of[osm_id]}"
                )
            if unit_of.setdefault(osm_id, data["unit"]) != data["unit"]:
                raise ShapePointError(
                    f"OSM node {osm_id} lies inside edge {key} and inside another road"
                )


def _indptr(lengths: list[int]) -> NDArray[np.int64]:
    return np.concatenate(([0], np.cumsum(lengths, dtype=np.int64))).astype(np.int64)


def _concat(runs: list[Any]) -> NDArray[np.int64]:
    return np.array([x for run in runs for x in run], dtype=np.int64)


def _lookup(index: dict[str, int], values: list[str | None]) -> NDArray[np.int32]:
    return np.array([-1 if v is None else index[v] for v in values], dtype=np.int32)


def build_params(region: Region, build_dir: Path) -> dict[str, Any]:
    """The build's params: the region, its bbox and CRS, and each step's recorded configuration.

    The configuration is what the cleanup, enrich and tiers sidecars recorded (their
    `allow_fragmented` and `config`), without the pipeline version, which `meta` keeps apart,
    and save's own (`parameters()`), under "save".
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
    params["save"] = {"config": parameters()}
    return params


def parameters() -> dict[str, Any]:
    """The config values save depends on, JSON-ready: the geometry's simplification."""
    return {"GEOMETRY_SIMPLIFY_M": GEOMETRY_SIMPLIFY_M}


def build_sources(region: Region, build_dir: Path) -> list[dict[str, Any]]:
    """The extract `filter` read for this build: file, md5 and data date.

    Raises `SourcesChangedError` when the filtered roads are no longer the ones `build` read
    (their md5 against the one the build stage's sidecar recorded): the filter's record then
    describes another build's extract.
    """
    roads = layout.roads_pbf(region.name)
    built = json.loads(layout.sidecar(layout.build_stage(build_dir)).read_text())
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
        "format": FORMAT_VERSION,
        "params": params,
        "sources": sources,
    }


def graph_geometry(graph: nx.MultiDiGraph, data: GraphData) -> Geometry:
    """Each edge's shape, simplified and encoded, as the build's geometry."""
    keys: list[EdgeKey] = sorted(graph.edges(keys=True))
    shapes = np.asarray([graph.edges[key]["geometry"] for key in keys], dtype=object)
    simple = shapely.simplify(shapes, GEOMETRY_SIMPLIFY_M, preserve_topology=False)
    points, owner = shapely.get_coordinates(simple, return_index=True)
    indptr = np.concatenate(([0], np.cumsum(np.bincount(owner, minlength=len(keys)))))
    return encode(data, points, indptr.astype(np.int64))


def save_build(data: GraphData, build_dir: Path, geometry: Geometry) -> SaveReport:
    """Write the build into `build_dir`, check it reads back the same, write `save.json`."""
    clock = time.perf_counter()
    version = write_build(data, build_dir, geometry)
    loaded = read_build(build_dir, mmap=False)
    shapes = read_geometry(build_dir, data.m, mmap=False)
    written_back: list[tuple[str, Any, Any]] = [(name, data, loaded) for name in ARRAY_SPECS]
    written_back += [(name, geometry, shapes) for name in GEOMETRY_SPECS]
    for name, ours, theirs in written_back:
        written, read = getattr(ours, name), getattr(theirs, name)
        if written.dtype != read.dtype or not np.array_equal(written, read):
            raise RuntimeError(f"{build_dir}: {name} reads back different from what was written")
    meta = {k: v for k, v in loaded.meta.items() if k != "geometry_version"}
    if loaded.strings != data.strings or meta != {**data.meta, "graph_version": version}:
        raise RuntimeError(f"{build_dir}: strings or meta read back different")
    size = sum(p.stat().st_size for p in (build_dir / "arrays").iterdir())
    size += (build_dir / "strings.json").stat().st_size
    shape_size = sum(p.stat().st_size for p in (build_dir / "geometry").iterdir())
    report = SaveReport(
        graph_version=version,
        counts=dict(data.meta["counts"]),
        megabytes=round(size / 1e6, 1),
        geometry_megabytes=round(shape_size / 1e6, 1),
        seconds=round(time.perf_counter() - clock, 3),
    )
    layout.stage_file(build_dir, "save.json").write_text(
        json.dumps(asdict(report), indent=2) + "\n"
    )
    return report


def remove_build(build_dir: Path) -> None:
    """Delete a written build, `meta.json` first; the stages stay."""
    (build_dir / "meta.json").unlink(missing_ok=True)
    for directory in ("arrays", "geometry"):
        if (build_dir / directory).exists():
            shutil.rmtree(build_dir / directory)
    (build_dir / "strings.json").unlink(missing_ok=True)
