"""`pipeline qa`: a saved build's QA report, its GeoJSON layers and its thresholds.

`docs/PLAN.md` §2.8; `docs/reference/pipeline.md`, qa. `run_qa(build_dir)` reads the saved build,
the stage reports (`cleanup.json`, `enrich.json`, `tiers.json`, `save.json`) and the
super-junction records (`superjunctions`: the networkx engine's `stages/enrich.pkl`, the arrays
engine's `stages/cleanup/`), and writes under `qa/`:

- `twins.geojson`: one feature per twin pair, a MultiLineString of the two edges (along the
  build's geometry);
- `superjunctions.geojson`: one point per super-junction: kind, parts (the nodes it replaced),
  members, transitions (rows), in- and out-edges, and whether every (in, out) pair is allowed;
- `dropped_components.geojson`: one point per strongly connected component cleanup dropped and
  listed (the largest twenty), at its centroid, with its nodes and length;
- `tiers.geojson`: one point per node a tier dropped (its roads touch the node, but the node is
  outside the tier's largest component), with the tier;
- `qa.json`: counts by class (edges, and km with a two-way road counted once) and by tier, node
  kinds, the twins (cleanup's statistics, pairs found on the stage A graph and the coverage,
  and `pairs_saved`, the pairs the build holds after stage B carried them), the super-junction
  histogram (parts → super-junctions, by kind), the cleanup, enrich, tiers (without their node
  lists) and save reports, and the thresholds.

The thresholds (`Threshold`): twin coverage of one-way motorway and trunk length at least
`config.QA_TWIN_COVERAGE_MIN`, the share of nodes cleanup kept at least `SCC_MIN_RETAINED`, the
largest component it dropped at most `SCC_MAX_DROPPED_NODES` nodes, and each tier's share at
least `TIER_MIN_RETAINED`. Twin coverage with no one-way motorway or trunk to
measure passes as "n/a". `QaReport.passed` is their conjunction; the CLI exits 1 when it is
false, after writing everything.
"""

import json
import pickle
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from worst_route.config import (
    QA_TWIN_COVERAGE_MIN,
    SCC_MAX_DROPPED_NODES,
    SCC_MIN_RETAINED,
    TIER_MIN_RETAINED,
    TIERS,
    HighwayClass,
)
from worst_route.graph.data import GraphData, NodeKind
from worst_route.graph.geometry import Geometry, edge_points, read_geometry
from worst_route.graph.store import read_build
from worst_route.pipeline import layout
from worst_route.pipeline.arrays import cleaned

LAYERS = ("twins", "superjunctions", "dropped_components", "tiers")


@dataclass
class Threshold:
    """One QA threshold: `value` at least `minimum`, or at most `maximum` when that is set;
    `value` None when there is nothing to measure (it passes)."""

    name: str
    value: float | None
    minimum: float | None
    passed: bool
    maximum: float | None = None


@dataclass
class QaReport:
    """What `qa.json` holds."""

    region: str = ""
    build_id: str = ""
    graph_version: str = ""
    counts: dict[str, Any] = field(default_factory=dict)
    twins: dict[str, Any] = field(default_factory=dict)
    superjunctions: dict[str, dict[int, int]] = field(default_factory=dict)  # kind: parts -> n
    cleanup: dict[str, Any] = field(default_factory=dict)
    enrich: dict[str, Any] = field(default_factory=dict)
    tiers: dict[str, Any] = field(default_factory=dict)
    save: dict[str, Any] = field(default_factory=dict)
    thresholds: list[Threshold] = field(default_factory=list)
    passed: bool = True


def parameters() -> dict[str, Any]:
    """The config values the thresholds depend on, JSON-ready."""
    return {
        "QA_TWIN_COVERAGE_MIN": QA_TWIN_COVERAGE_MIN,
        "SCC_MIN_RETAINED": SCC_MIN_RETAINED,
        "SCC_MAX_DROPPED_NODES": SCC_MAX_DROPPED_NODES,
        "TIER_MIN_RETAINED": TIER_MIN_RETAINED,
    }


def run_qa(build_dir: Path) -> QaReport:
    """Write the build's QA report and layers under `qa/`; return the report."""
    data = read_build(build_dir)
    stages = {
        name: json.loads(layout.stage_file(build_dir, f"{name}.json").read_text())
        for name in ("cleanup", "enrich", "tiers", "save")
    }
    records = superjunctions(build_dir)
    cleanup, tiers = stages["cleanup"], stages["tiers"]
    report = QaReport(
        region=data.meta["region"],
        build_id=data.meta["build_id"],
        graph_version=data.meta["graph_version"],
        counts=_counts(data),
        twins={**cleanup["twins"], "pairs_saved": len(data.twin_edges) // 2},
        cleanup=cleanup,
        enrich=stages["enrich"],
        tiers={
            **tiers,
            "tiers": [{k: v for k, v in t.items() if k != "node_ids"} for t in tiers["tiers"]],
        },
        save=stages["save"],
    )
    histogram: dict[str, Counter[int]] = {"interchange": Counter(), "roundabout": Counter()}
    for kind, parts in records.values():
        histogram[kind][parts] += 1
    report.superjunctions = {kind: dict(sorted(c.items())) for kind, c in histogram.items()}

    coverage = cleanup["twins"]["coverage"]
    largest = max((c["nodes"] for c in cleanup["connectivity"]["largest_dropped"]), default=0)
    report.thresholds = [
        Threshold(
            "twin coverage",
            coverage,
            QA_TWIN_COVERAGE_MIN,
            coverage is None or coverage >= QA_TWIN_COVERAGE_MIN,
        ),
        _threshold("SCC retained", cleanup["connectivity"]["retained"], SCC_MIN_RETAINED),
        Threshold(
            "SCC largest dropped",
            largest,
            None,
            largest <= SCC_MAX_DROPPED_NODES,
            SCC_MAX_DROPPED_NODES,
        ),
        *(
            _threshold(f"{t['name']} retained", t["retained"], TIER_MIN_RETAINED)
            for t in tiers["tiers"]
        ),
    ]
    report.passed = all(t.passed for t in report.thresholds)

    layers = {
        "twins": _twins(data, read_geometry(build_dir, data.m)),
        "superjunctions": _superjunctions(data, records),
        "dropped_components": [
            _point(c["lon"], c["lat"], {"nodes": c["nodes"], "length_m": round(c["length_m"], 1)})
            for c in cleanup["connectivity"]["largest_dropped"]
        ],
        "tiers": _tier_drops(data),
    }
    layout.qa_file(build_dir, "qa.json").parent.mkdir(exist_ok=True)
    for name, features in layers.items():
        collection = {"type": "FeatureCollection", "features": features}
        layout.qa_file(build_dir, f"{name}.geojson").write_text(json.dumps(collection) + "\n")
    layout.qa_file(build_dir, "qa.json").write_text(json.dumps(asdict(report), indent=2) + "\n")
    return report


def _threshold(name: str, value: float, minimum: float) -> Threshold:
    return Threshold(name, value, minimum, value >= minimum)


def _counts(data: GraphData) -> dict[str, Any]:
    once = data.edge_unit == np.arange(data.m)  # one edge per road unit
    by_class = {}
    for c in HighwayClass:
        edges = data.edge_highway == c
        if edges.any():
            km = float(data.edge_length_m[edges & once].astype(np.float64).sum()) / 1000
            by_class[c.name.lower()] = {"edges": int(edges.sum()), "km": round(km, 1)}
    kinds = Counter(NodeKind(k).name.lower() for k in data.node_kind.tolist())
    return {
        "nodes": data.n,
        "edges": data.m,
        "km": round(float(data.edge_length_m[once].astype(np.float64).sum()) / 1000, 1),
        "node_kinds": {k.name.lower(): kinds[k.name.lower()] for k in NodeKind},
        "by_class": by_class,
        "by_tier": {
            f"T{t}": {
                "nodes": int(data.tier_node_mask[t].sum()),
                "edges": int(data.tier_edge_mask[t].sum()),
            }
            for t in range(len(TIERS))
        },
    }


def _line(data: GraphData, geometry: Geometry, e: int) -> list[list[float]]:
    points, _ = edge_points(data, geometry, np.array([e]))
    return np.round(points, 7).tolist()  # type: ignore[no-any-return]


def _point(lon: float, lat: float, properties: dict[str, Any]) -> dict[str, Any]:
    coordinates = [round(float(lon), 7), round(float(lat), 7)]
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": properties,
    }


def _identity(data: GraphData, e: int) -> str | None:
    i = int(data.edge_identity[e])
    return None if i < 0 else data.strings.identities[i]


def _twins(data: GraphData, geometry: Geometry) -> list[dict[str, Any]]:
    rows = np.repeat(np.arange(data.m), np.diff(data.twin_indptr))
    features = []
    for a, b in zip(rows.tolist(), data.twin_edges.tolist(), strict=True):
        if a < b:  # each pair once
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [_line(data, geometry, a), _line(data, geometry, b)],
                    },
                    "properties": {
                        "edges": [a, b],
                        "highway": [
                            HighwayClass(data.edge_highway[e]).name.lower() for e in (a, b)
                        ],
                        "identity": [_identity(data, e) for e in (a, b)],
                        "length_m": [round(float(data.edge_length_m[e]), 1) for e in (a, b)],
                    },
                }
            )
    return features


def superjunctions(build_dir: Path) -> dict[int, tuple[str, int]]:
    """Each super-junction's kind and number of parts, from the records of the build's stages:
    the networkx engine's enriched graph, or the arrays engine's cleaned graph."""
    if layout.cleanup_engine(build_dir) == "arrays":
        graph = cleaned.load_stage(build_dir)
        parts = np.diff(graph.part_indptr).tolist()
        kinds = [NodeKind(k).name.lower() for k in graph.node_kind[graph.super_node].tolist()]
        return dict(zip(graph.super_node.tolist(), zip(kinds, parts, strict=True), strict=True))
    with layout.stage_file(build_dir, "enrich.pkl").open("rb") as f:
        records: dict[int, dict[str, Any]] = pickle.load(f).graph["superjunctions"]
    return {node: (record["kind"], len(record["parts"])) for node, record in records.items()}


def _superjunctions(data: GraphData, records: dict[int, tuple[str, int]]) -> list[dict[str, Any]]:
    rows = Counter(data.trans_node.tolist())
    features = []
    for node, (kind, parts) in sorted(records.items()):
        ins = int(data.in_indptr[node + 1] - data.in_indptr[node])
        outs = int(data.out_indptr[node + 1] - data.out_indptr[node])
        properties = {
            "node": node,
            "kind": kind,
            "parts": parts,
            "members": int(data.member_indptr[node + 1] - data.member_indptr[node]),
            "transitions": rows[node],
            "in": ins,
            "out": outs,
            "every_turn": rows[node] == ins * outs,
        }
        features.append(_point(data.node_lon[node], data.node_lat[node], properties))
    return features


def _tier_drops(data: GraphData) -> list[dict[str, Any]]:
    features = []
    for t, classes in enumerate(TIERS):
        roads = np.isin(data.edge_highway, sorted(classes))
        touched = np.zeros(data.n, dtype=np.bool_)
        touched[data.edge_u[roads]] = touched[data.edge_v[roads]] = True
        for node in np.flatnonzero(touched & ~data.tier_node_mask[t]).tolist():
            features.append(
                _point(data.node_lon[node], data.node_lat[node], {"tier": f"T{t}", "node": node})
            )
    return features
