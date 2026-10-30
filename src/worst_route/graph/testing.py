"""Small hand-built graphs for tests (`docs/reference/graph.md`, **Test builders**).

`make_graph(nodes, edges, ...)` turns node coordinates (x, y in metres in `crs_epsg`) and an
edge list into a valid, read-only `GraphData`, filling every array the pipeline would with a
plain default: lengths as given (Euclidean when omitted), bearings from the coordinates, one
OSM member per node (id = node index + 1), one way per edge (id = input position + 1), class
residential (tier 4), speed 40 km/h, the unit id = the edge id, pass-through from the topology,
tier masks from the edge tiers (no SCC). Edge ids are the positions after a stable sort by
(u, v), as in a real build; twins and transitions name edges by their (u, v) node pairs, and
`edge_id(data, u, v)` finds an edge the same way.

`grid_graph(rows, cols, spacing_m, ...)` builds a lattice at `GRID_ORIGIN` (UTM 10N, near
downtown Portland): node r * cols + c sits at (x0 + c * spacing, y0 + r * spacing), row 0 the
southmost. `oneway_rows` and `oneway_cols` give one character per row or column: `+` one-way
east (rows) or north (columns), `-` the opposite, `.` two-way; an empty string is two-way
everywhere.

It ships in the package so every area's tests can build on it; nothing outside tests uses it.
"""

import math
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray
from pyproj import Transformer

from worst_route.config import TIERS, HighwayClass
from worst_route.graph.data import ARRAY_SPECS, N_TIERS, EdgeFlag, GraphData, Strings

GRID_ORIGIN = (525_000.0, 5_040_000.0)

_LINKS = frozenset(c for c in HighwayClass if c.name.endswith("_LINK"))

EdgePair = tuple[tuple[int, int], tuple[int, int]]


class Edge(NamedTuple):
    """One edge for `make_graph`; `length_m=None` means the straight-line distance."""

    u: int
    v: int
    length_m: float | None = None
    highway: HighwayClass = HighwayClass.RESIDENTIAL
    speed_kmh: float = 40.0
    name: str | None = None
    ref: str | None = None
    flags: int = 0
    toll_booths: int = 0
    level_crossings: int = 0


def make_graph(
    nodes: Sequence[tuple[float, float]],
    edges: Sequence[tuple[int, int, float]] | Sequence[Edge],
    *,
    twins: Sequence[EdgePair] = (),
    transitions: Sequence[EdgePair] = (),
    crs_epsg: int = 32610,
    lonlat: Sequence[tuple[float, float]] | None = None,
) -> GraphData:
    """A valid `GraphData` from node coordinates and edges, every other array defaulted.

    `twins` pairs edges, each named by its (u, v): the symmetric closure is stored.
    `transitions` lists allowed ((a, v), (v, b)) pairs at node v.
    `lonlat` overrides the inverse projection of the node coordinates.
    """
    n = len(nodes)
    xy = np.array(nodes, dtype=np.float64).reshape(n, 2)
    if lonlat is None:
        to_lonlat = Transformer.from_crs(f"EPSG:{crs_epsg}", "EPSG:4326", always_xy=True)
        lon, lat = to_lonlat.transform(xy[:, 0], xy[:, 1])
        ll = np.column_stack([lon, lat]).astype(np.float64)
    else:
        ll = np.array(lonlat, dtype=np.float64).reshape(n, 2)

    given = [e if isinstance(e, Edge) else Edge(e[0], e[1], e[2]) for e in edges]
    order = sorted(range(len(given)), key=lambda k: (given[k].u, given[k].v, k))
    specs = [given[k] for k in order]
    m = len(specs)

    names: list[str] = []
    refs: list[str] = []
    identities: list[str] = []

    def index(table: list[str], value: str | None) -> int:
        if value is None:
            return -1
        if value not in table:
            table.append(value)
        return table.index(value)

    lengths: list[float] = []
    bearings: list[int] = []
    tiers: list[int] = []
    flags: list[int] = []
    name_ids: list[int] = []
    ref_ids: list[int] = []
    identity_ids: list[int] = []
    for s in specs:
        dx, dy = xy[s.v] - xy[s.u]
        lengths.append(float(math.hypot(dx, dy)) if s.length_m is None else s.length_m)
        bearings.append(round(math.degrees(math.atan2(dx, dy))) % 360)
        tiers.append(next(t for t, classes in enumerate(TIERS) if s.highway in classes))
        flags.append(s.flags | (EdgeFlag.LINK if s.highway in _LINKS else 0))
        name_ids.append(index(names, s.name))
        ref_ids.append(index(refs, s.ref))
        identity_ids.append(index(identities, s.ref if s.ref is not None else s.name))

    edge_u = np.array([s.u for s in specs], dtype=np.int32)
    edge_v = np.array([s.v for s in specs], dtype=np.int32)
    speeds = [s.speed_kmh for s in specs]
    edge_tier = np.array(tiers, dtype=np.uint8)
    bearing = np.array(bearings, dtype=np.int16)
    osm_ids = np.arange(1, n + 1, dtype=np.int64)

    tails: list[set[int]] = [set() for _ in range(n)]
    heads: list[set[int]] = [set() for _ in range(n)]
    for s in specs:
        if s.u != s.v:
            heads[s.u].add(s.v)
            tails[s.v].add(s.u)
    pass_through = [bool(tails[v] and heads[v] and len(tails[v] | heads[v]) >= 2) for v in range(n)]

    by_pair: dict[tuple[int, int], list[int]] = {}
    for e, s in enumerate(specs):
        by_pair.setdefault((s.u, s.v), []).append(e)

    def find(pair: tuple[int, int]) -> int:
        found = by_pair.get(pair, [])
        if len(found) != 1:
            raise ValueError(f"edge {pair}: {len(found)} edges match, expected exactly one")
        return found[0]

    twin_sets: list[set[int]] = [set() for _ in range(m)]
    for first, second in twins:
        a, b = find(first), find(second)
        twin_sets[a].add(b)
        twin_sets[b].add(a)
    allowed = sorted({(int(edge_v[find(i)]), find(i), find(o)) for i, o in transitions})
    trans = np.array(allowed, dtype=np.int32).reshape(len(allowed), 3)

    tier_edge_mask = np.array([edge_tier <= t for t in range(N_TIERS)], dtype=np.bool_)
    tier_node_mask = np.zeros((N_TIERS, n), dtype=np.bool_)
    for t in range(N_TIERS):
        tier_node_mask[t, edge_u[tier_edge_mask[t]]] = True
        tier_node_mask[t, edge_v[tier_edge_mask[t]]] = True

    data = GraphData(
        node_lon=ll[:, 0].copy(),
        node_lat=ll[:, 1].copy(),
        node_x=xy[:, 0].copy(),
        node_y=xy[:, 1].copy(),
        node_kind=np.zeros(n, dtype=np.uint8),
        node_state=np.full(n, -1, dtype=np.int16),
        node_pass_through=np.array(pass_through, dtype=np.bool_).reshape(n),
        node_delay_s=np.zeros(n, dtype=np.float32),
        member_indptr=np.arange(n + 1, dtype=np.int64),
        member_osm_ids=osm_ids,
        edge_u=edge_u,
        edge_v=edge_v,
        edge_length_m=np.array(lengths, dtype=np.float32),
        edge_speed_kmh=np.array(speeds, dtype=np.float32),
        edge_time_s=np.array(
            [x / (v / 3.6) for x, v in zip(lengths, speeds, strict=True)], dtype=np.float32
        ),
        edge_highway=np.array([s.highway for s in specs], dtype=np.uint8),
        edge_tier=edge_tier,
        edge_name=np.array(name_ids, dtype=np.int32),
        edge_ref=np.array(ref_ids, dtype=np.int32),
        edge_identity=np.array(identity_ids, dtype=np.int32),
        edge_unit=np.arange(m, dtype=np.int32),
        edge_flags=np.array(flags, dtype=np.uint8),
        edge_toll_booths=np.array([s.toll_booths for s in specs], dtype=np.uint8),
        edge_level_crossings=np.array([s.level_crossings for s in specs], dtype=np.uint8),
        edge_bearing_in=bearing,
        edge_bearing_out=bearing.copy(),
        way_indptr=np.arange(m + 1, dtype=np.int64),
        way_osm_ids=np.array(order, dtype=np.int64) + 1,
        out_indptr=_indptr(edge_u, n),
        out_edges=np.arange(m, dtype=np.int32),
        in_indptr=_indptr(edge_v, n),
        in_edges=np.argsort(edge_v, kind="stable").astype(np.int32),
        coord_indptr=np.arange(0, 2 * m + 1, 2, dtype=np.int64),
        coords=np.stack([ll[edge_u], ll[edge_v]], axis=1).reshape(2 * m, 2).astype(np.float32),
        osmnode_indptr=np.arange(0, 2 * m + 1, 2, dtype=np.int64),
        osmnode_ids=np.stack([osm_ids[edge_u], osm_ids[edge_v]], axis=1).reshape(2 * m),
        twin_indptr=np.concatenate(([0], np.cumsum([len(t) for t in twin_sets]))).astype(np.int64),
        twin_edges=np.array([b for t in twin_sets for b in sorted(t)], dtype=np.int32),
        trans_node=trans[:, 0].copy(),
        trans_in=trans[:, 1].copy(),
        trans_out=trans[:, 2].copy(),
        tier_node_mask=tier_node_mask,
        tier_edge_mask=tier_edge_mask,
        strings=Strings(names=names, refs=refs, identities=identities),
        meta={
            "region": "test",
            "build_id": "test",
            "crs_epsg": crs_epsg,
            "created": "1970-01-01T00:00:00+00:00",
            "pipeline_version": "test",
            "graph_version": "",
            "counts": {"nodes": n, "edges": m},
            "params": {},
            "sources": [],
        },
    )
    for name in ARRAY_SPECS:
        getattr(data, name).flags.writeable = False
    data.validate()
    return data


def grid_graph(
    rows: int,
    cols: int,
    spacing_m: float,
    *,
    oneway_rows: str = "",
    oneway_cols: str = "",
) -> GraphData:
    """A rows × cols lattice of residential streets, `spacing_m` apart, at `GRID_ORIGIN`."""
    row_dirs = _directions(oneway_rows, rows, "oneway_rows")
    col_dirs = _directions(oneway_cols, cols, "oneway_cols")
    x0, y0 = GRID_ORIGIN
    nodes = [(x0 + c * spacing_m, y0 + r * spacing_m) for r in range(rows) for c in range(cols)]
    edges: list[Edge] = []
    for r in range(rows):
        for c in range(cols - 1):
            edges += _street(r * cols + c, r * cols + c + 1, row_dirs[r], spacing_m)
    for c in range(cols):
        for r in range(rows - 1):
            edges += _street(r * cols + c, (r + 1) * cols + c, col_dirs[c], spacing_m)
    return make_graph(nodes, edges)


def edge_id(data: GraphData, u: int, v: int) -> int:
    """The id of the one edge u → v; `ValueError` if there is none or more than one."""
    run = data.out_edges[data.out_indptr[u] : data.out_indptr[u + 1]]
    found = run[data.edge_v[run] == v]
    if len(found) != 1:
        raise ValueError(f"edge ({u}, {v}): {len(found)} edges match, expected exactly one")
    return int(found[0])


def _indptr(endpoint: NDArray[np.int32], n: int) -> NDArray[np.int64]:
    return np.concatenate(([0], np.cumsum(np.bincount(endpoint, minlength=n)))).astype(np.int64)


def _directions(spec: str, count: int, what: str) -> str:
    if spec == "":
        return "." * count
    if len(spec) != count or set(spec) - set("+-."):
        raise ValueError(f"{what}: {spec!r} must be {count} characters from '+', '-', '.'")
    return spec


def _street(a: int, b: int, direction: str, length: float) -> list[Edge]:
    forward, backward = Edge(a, b, length), Edge(b, a, length)
    return {"+": [forward], "-": [backward], ".": [forward, backward]}[direction]
