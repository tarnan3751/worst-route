"""How well a saved build's bearings describe the roads at its junctions, by node kind.

    uv run python tools/turn_bearings.py --region portland [--build-id ID]

It needs a build under `WORST_ROUTE_DATA`, so it is not part of the gate. Cleanup extends an
edge's geometry to a merged node's position with stub coordinates: one at an end on a stage A
cluster or a super-node, two where the super-node took in a stage A cluster. Enrich measures the
bearings over the road without them (`pipeline/enrich.py`, `road_geometry`). This tool checks
that from the build alone, by its own count of the stubs: every coordinate of an edge is one of
its OSM nodes' (`osmnode_ids`) or a stub, so an edge has len(coords) - len(osmnode_ids) of
them, all at its merged end when only one end is merged, split evenly when both are (an odd
count there cannot be split, and its turns are skipped).

For every turn between two roads (an edge in, an edge out of a different road unit) at every
node, it classes the turn (`graph.backend.TURN_CLASS`) three ways: by the stored bearings, by
bearings over the road (the geometry less the stubs, measured as enrich does over
`BEARING_SAMPLE_M`), and by bearings over the whole geometry, stubs included (what builds before
pipeline version 2 stored). It prints, per node kind, the turns compared, how many the stored
bearings and the whole geometry class differently from the road, and the largest gap in degrees
between a stored bearing and the road's at that kind of node. The stored bearings differ from
the road's only by the rounding of the build's float32 coordinates, a degree or two, so a
handful of turns near a class's threshold flip, at every kind of node alike. It exits 0.
"""

import argparse
import sys
from collections import Counter

import numpy as np
import shapely
from numpy.typing import NDArray
from pyproj import Transformer

from worst_route.config import REGIONS
from worst_route.graph.backend import TURN_CLASS
from worst_route.graph.data import GraphData, NodeKind
from worst_route.graph.store import read_build
from worst_route.pipeline import layout
from worst_route.pipeline.enrich import bearings

NAMES = {0: "straight", 1: "left", 2: "right", 4: "u-turn"}


def edge_lines(data: GraphData) -> tuple[list[shapely.LineString], list[shapely.LineString | None]]:
    """Each edge's whole geometry and its road (None where the stubs cannot be split), in the
    build's projected CRS."""
    project = Transformer.from_crs("EPSG:4326", f"EPSG:{data.meta['crs_epsg']}", always_xy=True)
    lonlat = np.asarray(data.coords, dtype=np.float64)
    x, y = project.transform(lonlat[:, 0], lonlat[:, 1])
    xy = np.column_stack((x, y))
    merged = np.asarray(data.node_kind) != NodeKind.PLAIN
    wholes: list[shapely.LineString] = []
    roads: list[shapely.LineString | None] = []
    for e in range(data.m):
        line = xy[data.coord_indptr[e] : data.coord_indptr[e + 1]]
        wholes.append(shapely.LineString(line))
        stubs = len(line) - int(data.osmnode_indptr[e + 1] - data.osmnode_indptr[e])
        at_u, at_v = bool(merged[data.edge_u[e]]), bool(merged[data.edge_v[e]])
        if at_u and at_v and stubs % 2:
            roads.append(None)
            continue
        start = stubs // 2 if at_u and at_v else stubs if at_u else 0
        road = line[start : len(line) - (stubs - start)]
        fits = len(road) >= 2 and shapely.LineString(road).length > 0
        roads.append(shapely.LineString(road) if fits else wholes[-1])
    return wholes, roads


def turn(b_in: NDArray[np.int64], b_out: NDArray[np.int64], e: int, f: int) -> int:
    return int(TURN_CLASS[(int(b_out[f]) - int(b_in[e])) % 360])


def main(args: argparse.Namespace) -> int:
    build_id = args.build_id or layout.latest_file(args.region).read_text().strip()
    data = read_build(layout.build_dir(args.region, build_id))
    wholes, roads = edge_lines(data)
    whole_in, whole_out = bearings(wholes)
    fit = [k for k, line in enumerate(roads) if line is not None]
    road_in = np.full(data.m, -1, dtype=np.int64)
    road_out = np.full(data.m, -1, dtype=np.int64)
    road_in[fit], road_out[fit] = bearings([line for line in roads if line is not None])
    stored_in = np.asarray(data.edge_bearing_in, dtype=np.int64)
    stored_out = np.asarray(data.edge_bearing_out, dtype=np.int64)
    kinds = np.asarray(data.node_kind)
    gap_in = np.where(road_in >= 0, np.abs((road_in - stored_in + 180) % 360 - 180), 0)
    gap_out = np.where(road_out >= 0, np.abs((road_out - stored_out + 180) % 360 - 180), 0)

    counts: dict[NodeKind, Counter[str]] = {kind: Counter() for kind in NodeKind}
    stored_off: dict[NodeKind, Counter[tuple[str, str]]] = {kind: Counter() for kind in NodeKind}
    for v in range(data.n):
        kind = NodeKind(int(data.node_kind[v]))
        ins = data.in_edges[data.in_indptr[v] : data.in_indptr[v + 1]].tolist()
        outs = data.out_edges[data.out_indptr[v] : data.out_indptr[v + 1]].tolist()
        for e in ins:
            for f in outs:
                if data.edge_unit[e] == data.edge_unit[f]:
                    continue
                if road_in[e] < 0 or road_out[f] < 0:
                    counts[kind]["skipped"] += 1
                    continue
                road = turn(road_in, road_out, e, f)
                stored = turn(stored_in, stored_out, e, f)
                counts[kind]["turns"] += 1
                counts[kind]["stored differs"] += stored != road
                counts[kind]["whole differs"] += turn(whole_in, whole_out, e, f) != road
                if stored != road:
                    stored_off[kind][(NAMES[road], NAMES[stored])] += 1

    print(f"build: {args.region}/{build_id} (pipeline version {data.meta['pipeline_version']})")
    print(
        f"{'node kind':12s} {'turns':>8s} {'stored ≠ road':>16s} {'whole ≠ road':>16s} "
        "skipped  max gap"
    )
    for kind in NodeKind:
        c = counts[kind]
        n = max(c["turns"], 1)
        gap = max(
            gap_in[kinds[data.edge_v] == kind].max(initial=0),
            gap_out[kinds[data.edge_u] == kind].max(initial=0),
        )
        print(
            f"{kind.name.lower():12s} {c['turns']:8d} "
            f"{c['stored differs']:7d} ({100 * c['stored differs'] / n:5.2f} %) "
            f"{c['whole differs']:7d} ({100 * c['whole differs'] / n:5.2f} %) {c['skipped']:7d} "
            f"{gap:5d}°"
        )
    for kind in NodeKind:
        for (road_says, stored_says), n in stored_off[kind].most_common(4):
            print(f"  {kind.name.lower()}: road {road_says}, stored {stored_says}: {n}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--region", required=True, choices=sorted(REGIONS))
    parser.add_argument("--build-id", default=None, help="default: the region's latest")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main(parse_args()))
