"""The arrays of one graph build (`docs/reference/graph.md`, **The arrays**).

`GraphData` holds every array of a build as a NumPy array (read-only, and memory-mapped when
read from disk), plus `strings`, the name, ref and identity tables the edges index into and the
brand tables the brand ids index into, and `meta`. `ARRAY_SPECS` is the format (`FORMAT_VERSION`
4): each array's name, dtype and shape, in the order the build lists its files and
`graph_version` hashes them. n is the node count, m the edge count, T the number of tiers, b the
number of brands. Edge ids are sorted by (u, v, original order), so each node's out-edges are a
contiguous run of ascending ids. Brand k's nodes are
`brand_nodes[brand_indptr[k]:brand_indptr[k + 1]]` (`nodes_of_brand`), ascending, with the POIs
snapped to each in `brand_locations`. The shape of each edge is not among the arrays: it is
the build's geometry (`graph/geometry.py`), which the engine never reads.

Three arrays are derived, not stored, each worked out once per `GraphData` on first use and
read-only: `node_lon` and `node_lat`, the inverse projection of `node_x` and `node_y` from
`meta["crs_epsg"]` (the engine reads x/y, the presentation lon/lat), and `edge_speed_kmh`,
`speed_kmh[edge_speed]` (a build's speeds are few: `edge_speed` is each edge's index into its
table of distinct speeds).

`validate()` checks a build against the format and raises `ValueError` naming the array.
`deep=False` checks only what is cheap without reading the arrays' contents (dtypes, shapes,
the meta keys and counts, and `meta["params"]` JSON-native with string keys, so that its JSON,
which `graph_version` hashes, is the params); that is what `read_build` runs. The default also
checks contents: indptr runs, id and enum ranges, the speed table, each original OSM node id a
member of one node only, the edge order, both adjacency lists, twins symmetric, transitions
sorted, roundabout rings counted at super-junctions only, and each brand's nodes in range,
strictly ascending, each with at least one location.
"""

import json
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from functools import cached_property
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pyproj import Transformer

from worst_route.config import TIERS, HighwayClass

N_TIERS = len(TIERS)
FORMAT_VERSION = 4  # meta["format"]: ARRAY_SPECS and the build's files (`graph/store.py`)
MAX_SPEEDS = 256  # the distinct speeds a build's uint8 edge_speed can index


class NodeKind(IntEnum):
    """What a node stands for, stored as `node_kind` (uint8)."""

    PLAIN = 0
    CLUSTER = 1  # stage A: a signal cluster or a divided-arterial crossing
    INTERCHANGE = 2  # stage B super-junction
    ROUNDABOUT = 3  # stage B super-junction


class EdgeFlag(IntFlag):
    """The bits of `edge_flags` (uint8)."""

    BRIDGE = 1
    TUNNEL = 2
    TOLL = 4
    ROUNDABOUT = 8
    LINK = 16


# name -> (dtype, shape). Shape symbols: "n" nodes, "m" edges, "k" transition rows (the length
# of trans_node), "T" tiers, "r" brand rows (the length of brand_nodes), "s" speeds (the length
# of speed_kmh, at most MAX_SPEEDS), "*" any length; "n+1", "m+1" and "b+1" (b brands: the
# length of strings.brands) for indptr arrays.
ARRAY_SPECS: dict[str, tuple[np.dtype[Any], tuple[str | int, ...]]] = {
    # nodes
    "node_x": (np.dtype(np.float64), ("n",)),
    "node_y": (np.dtype(np.float64), ("n",)),
    "node_kind": (np.dtype(np.uint8), ("n",)),
    "node_pass_through": (np.dtype(np.bool_), ("n",)),
    "node_delay_s": (np.dtype(np.float32), ("n",)),
    "member_indptr": (np.dtype(np.int64), ("n+1",)),
    "member_osm_ids": (np.dtype(np.int64), ("*",)),
    # edges
    "edge_u": (np.dtype(np.int32), ("m",)),
    "edge_v": (np.dtype(np.int32), ("m",)),
    "edge_length_m": (np.dtype(np.float32), ("m",)),
    "edge_speed": (np.dtype(np.uint8), ("m",)),
    "speed_kmh": (np.dtype(np.float32), ("s",)),
    "edge_time_s": (np.dtype(np.float32), ("m",)),
    "edge_highway": (np.dtype(np.uint8), ("m",)),
    "edge_tier": (np.dtype(np.uint8), ("m",)),
    "edge_name": (np.dtype(np.int32), ("m",)),
    "edge_ref": (np.dtype(np.int32), ("m",)),
    "edge_identity": (np.dtype(np.int32), ("m",)),
    "edge_unit": (np.dtype(np.int32), ("m",)),
    "edge_flags": (np.dtype(np.uint8), ("m",)),
    "edge_toll_booths": (np.dtype(np.uint8), ("m",)),
    "edge_level_crossings": (np.dtype(np.uint8), ("m",)),
    "edge_bearing_in": (np.dtype(np.int16), ("m",)),
    "edge_bearing_out": (np.dtype(np.int16), ("m",)),
    # adjacency
    "out_indptr": (np.dtype(np.int64), ("n+1",)),
    "out_edges": (np.dtype(np.int32), ("m",)),
    "in_indptr": (np.dtype(np.int64), ("n+1",)),
    "in_edges": (np.dtype(np.int32), ("m",)),
    # twins
    "twin_indptr": (np.dtype(np.int64), ("m+1",)),
    "twin_edges": (np.dtype(np.int32), ("*",)),
    # transitions
    "trans_node": (np.dtype(np.int32), ("k",)),
    "trans_in": (np.dtype(np.int32), ("k",)),
    "trans_out": (np.dtype(np.int32), ("k",)),
    # tiers
    "tier_node_mask": (np.dtype(np.bool_), ("T", "n")),
    "tier_edge_mask": (np.dtype(np.bool_), ("T", "m")),
    # collectibles
    "node_roundabouts": (np.dtype(np.uint8), ("n",)),
    # brands: brand k's nodes, ascending, and the POIs snapped to each
    "brand_indptr": (np.dtype(np.int64), ("b+1",)),
    "brand_nodes": (np.dtype(np.int32), ("r",)),
    "brand_locations": (np.dtype(np.int32), ("r",)),
}

META_KEYS = (
    "region",
    "build_id",
    "crs_epsg",
    "created",
    "pipeline_version",
    "format",
    "graph_version",
    "counts",
    "params",
    "sources",
)


@dataclass(frozen=True)
class Strings:
    """The string tables `edge_name`, `edge_ref` and `edge_identity` index into (−1 = none), and
    each brand's display name and wikidata id (None when the brand has none), by brand id."""

    names: list[str]
    refs: list[str]
    identities: list[str]
    brands: list[str] = field(default_factory=list)
    brand_wikidata: list[str | None] = field(default_factory=list)


@dataclass(frozen=True, eq=False)
class GraphData:
    """Every array of one build; the fields are `ARRAY_SPECS` in order, then strings and meta."""

    # nodes
    node_x: NDArray[np.float64]  # projected metres in meta["crs_epsg"]
    node_y: NDArray[np.float64]
    node_kind: NDArray[np.uint8]  # NodeKind
    node_pass_through: NDArray[np.bool_]  # exists u→v→w with u ≠ w
    node_delay_s: NDArray[np.float32]
    member_indptr: NDArray[np.int64]
    member_osm_ids: NDArray[np.int64]  # original OSM node ids merged into each node
    # edges
    edge_u: NDArray[np.int32]
    edge_v: NDArray[np.int32]
    edge_length_m: NDArray[np.float32]
    edge_speed: NDArray[np.uint8]  # index into speed_kmh
    speed_kmh: NDArray[np.float32]  # the build's distinct speeds, ascending
    edge_time_s: NDArray[np.float32]
    edge_highway: NDArray[np.uint8]  # HighwayClass
    edge_tier: NDArray[np.uint8]  # the first tier whose classes contain the edge's class
    edge_name: NDArray[np.int32]  # index into strings.names, −1 none
    edge_ref: NDArray[np.int32]  # index into strings.refs, −1 none
    edge_identity: NDArray[np.int32]  # index into strings.identities, −1 none
    edge_unit: NDArray[np.int32]
    edge_flags: NDArray[np.uint8]  # EdgeFlag
    edge_toll_booths: NDArray[np.uint8]
    edge_level_crossings: NDArray[np.uint8]
    edge_bearing_in: NDArray[np.int16]  # degrees clockwise from north, arriving at v
    edge_bearing_out: NDArray[np.int16]  # degrees clockwise from north, leaving u
    # adjacency
    out_indptr: NDArray[np.int64]
    out_edges: NDArray[np.int32]  # each node's out-edges, ascending edge id
    in_indptr: NDArray[np.int64]
    in_edges: NDArray[np.int32]  # each node's in-edges, ascending edge id
    # twins: using an edge bans every edge in its row (symmetric)
    twin_indptr: NDArray[np.int64]
    twin_edges: NDArray[np.int32]
    # transitions: allowed (in, out) pairs, sorted by (node, in, out); a node without rows
    # allows every pair
    trans_node: NDArray[np.int32]
    trans_in: NDArray[np.int32]
    trans_out: NDArray[np.int32]
    # tiers
    tier_node_mask: NDArray[np.bool_]
    tier_edge_mask: NDArray[np.bool_]
    # collectibles: the roundabout rings each super-junction holds, 0 at every other node
    node_roundabouts: NDArray[np.uint8]
    # brands: brand k's nodes are brand_nodes[brand_indptr[k]:brand_indptr[k + 1]], ascending;
    # brand_locations the POIs snapped to each
    brand_indptr: NDArray[np.int64]
    brand_nodes: NDArray[np.int32]
    brand_locations: NDArray[np.int32]
    strings: Strings
    meta: dict[str, Any]

    @property
    def n(self) -> int:
        """The number of nodes."""
        return int(self.node_x.shape[0])

    @property
    def m(self) -> int:
        """The number of edges."""
        return int(self.edge_u.shape[0])

    @cached_property
    def node_lon(self) -> NDArray[np.float64]:
        """Each node's longitude: `node_x`, `node_y` projected back to EPSG:4326."""
        return self._lonlat[0]

    @cached_property
    def node_lat(self) -> NDArray[np.float64]:
        """Each node's latitude: `node_x`, `node_y` projected back to EPSG:4326."""
        return self._lonlat[1]

    @cached_property
    def _lonlat(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        to_lonlat = Transformer.from_crs(
            f"EPSG:{self.meta['crs_epsg']}", "EPSG:4326", always_xy=True
        )
        lon, lat = to_lonlat.transform(np.asarray(self.node_x), np.asarray(self.node_y))
        lonlat = (np.asarray(lon, dtype=np.float64), np.asarray(lat, dtype=np.float64))
        for a in lonlat:
            a.flags.writeable = False
        return lonlat

    @cached_property
    def edge_speed_kmh(self) -> NDArray[np.float32]:
        """Each edge's speed: `speed_kmh[edge_speed]`."""
        speeds: NDArray[np.float32] = self.speed_kmh[self.edge_speed]
        speeds.flags.writeable = False
        return speeds

    def nodes_of_brand(self, brand: int) -> NDArray[np.int32]:
        """Brand `brand`'s nodes, ascending."""
        return self.brand_nodes[self.brand_indptr[brand] : self.brand_indptr[brand + 1]]

    def validate(self, *, deep: bool = True) -> None:
        """Raise `ValueError` if the build breaks the format; `deep=False` skips contents."""
        self._check_structure()
        if deep:
            self._check_contents()

    def _check_structure(self) -> None:
        for name, (dtype, shape) in ARRAY_SPECS.items():
            a = getattr(self, name)
            if not isinstance(a, np.ndarray):
                raise ValueError(f"{name}: not a NumPy array")
            if a.dtype != dtype:
                raise ValueError(f"{name}: dtype {a.dtype}, expected {dtype}")
            if a.ndim != len(shape):
                raise ValueError(f"{name}: shape {a.shape}, expected {len(shape)} dimensions")
        n, m = self.n, self.m
        sizes: dict[str | int, str | int] = {"n": n, "n+1": n + 1, "m": m, "m+1": m + 1}
        sizes.update({"k": len(self.trans_node), "T": N_TIERS, "*": "*", 2: 2})
        sizes.update({"b+1": len(self.strings.brands) + 1, "r": len(self.brand_nodes)})
        sizes["s"] = len(self.speed_kmh)
        for name, (_, shape) in ARRAY_SPECS.items():
            actual = getattr(self, name).shape
            expected = tuple(sizes[s] for s in shape)
            if any(e not in ("*", a) for e, a in zip(expected, actual, strict=True)):
                raise ValueError(f"{name}: shape {actual}, expected {expected}")
        if len(self.speed_kmh) > MAX_SPEEDS:
            raise ValueError(f"speed_kmh: {len(self.speed_kmh)} speeds, at most {MAX_SPEEDS}")
        if len(self.strings.brand_wikidata) != len(self.strings.brands):
            raise ValueError("strings: brand_wikidata must list one entry per brand")
        missing = [key for key in META_KEYS if key not in self.meta]
        if missing:
            raise ValueError(f"meta: missing keys {missing}")
        if self.meta["format"] != FORMAT_VERSION:
            raise ValueError(f"meta: format {self.meta['format']}, expected {FORMAT_VERSION}")
        counts = self.meta["counts"]
        if counts.get("nodes") != n or counts.get("edges") != m:
            raise ValueError(f"meta: counts {counts} do not match {n} nodes and {m} edges")
        params = self.meta["params"]
        try:
            stable = json.loads(json.dumps(params, sort_keys=True)) == params
        except (TypeError, ValueError):
            stable = False
        if not stable:
            raise ValueError("meta: params must be JSON-native with string keys")

    def _check_contents(self) -> None:
        n, m = self.n, self.m
        _check_indptr("member_indptr", self.member_indptr, len(self.member_osm_ids), 1)
        if len(np.unique(self.member_osm_ids)) != len(self.member_osm_ids):
            raise ValueError("member_osm_ids: an original OSM node id is a member twice")
        _check_indptr("twin_indptr", self.twin_indptr, len(self.twin_edges), 0)
        _check_indptr("out_indptr", self.out_indptr, m, 0)
        _check_indptr("in_indptr", self.in_indptr, m, 0)
        _check_indptr("brand_indptr", self.brand_indptr, len(self.brand_nodes), 1)

        _check_range("node_kind", self.node_kind, 0, len(NodeKind))
        _check_range("edge_u", self.edge_u, 0, n)
        _check_range("edge_v", self.edge_v, 0, n)
        _check_range("edge_highway", self.edge_highway, 0, len(HighwayClass))
        _check_range("edge_tier", self.edge_tier, 0, N_TIERS)
        _check_range("edge_name", self.edge_name, -1, len(self.strings.names))
        _check_range("edge_ref", self.edge_ref, -1, len(self.strings.refs))
        _check_range("edge_identity", self.edge_identity, -1, len(self.strings.identities))
        _check_range("edge_unit", self.edge_unit, 0, m)
        _check_range("edge_speed", self.edge_speed, 0, len(self.speed_kmh))
        _check_range("edge_flags", self.edge_flags, 0, 1 << len(EdgeFlag))
        _check_range("edge_bearing_in", self.edge_bearing_in, 0, 360)
        _check_range("edge_bearing_out", self.edge_bearing_out, 0, 360)
        _check_range("twin_edges", self.twin_edges, 0, m)
        _check_range("trans_node", self.trans_node, 0, n)
        _check_range("trans_in", self.trans_in, 0, m)
        _check_range("trans_out", self.trans_out, 0, m)
        _check_range("brand_nodes", self.brand_nodes, 0, n)
        _check_range("brand_locations", self.brand_locations, 1, 1 << 31)
        supers = (self.node_kind == NodeKind.INTERCHANGE) | (self.node_kind == NodeKind.ROUNDABOUT)
        if (self.node_roundabouts[~supers] > 0).any():
            raise ValueError("node_roundabouts: only a super-junction holds roundabout rings")
        for name in ("edge_length_m", "edge_time_s"):
            values = getattr(self, name)
            if not (np.isfinite(values).all() and (values >= 0).all()):
                raise ValueError(f"{name}: values must be finite and non-negative")
        speeds = self.speed_kmh
        if not (np.isfinite(speeds).all() and (speeds > 0).all() and (np.diff(speeds) > 0).all()):
            raise ValueError("speed_kmh: speeds must be finite, positive and strictly ascending")

        key = self.edge_u.astype(np.int64) * n + self.edge_v
        if (np.diff(key) < 0).any():
            raise ValueError("edge_u, edge_v: edges must be sorted by (u, v)")
        out_indptr = np.concatenate(([0], np.cumsum(np.bincount(self.edge_u, minlength=n))))
        if not np.array_equal(self.out_indptr, out_indptr):
            raise ValueError("out_indptr: does not match the out-degree of each node")
        if not np.array_equal(self.out_edges, np.arange(m)):
            raise ValueError("out_edges: each node's out-edges must be listed in ascending id")
        in_indptr = np.concatenate(([0], np.cumsum(np.bincount(self.edge_v, minlength=n))))
        if not np.array_equal(self.in_indptr, in_indptr):
            raise ValueError("in_indptr: does not match the in-degree of each node")
        if not np.array_equal(self.in_edges, np.argsort(self.edge_v, kind="stable")):
            raise ValueError("in_edges: each node's in-edges must be listed in ascending id")

        rows = np.repeat(np.arange(m, dtype=np.int64), np.diff(self.twin_indptr))
        cols = self.twin_edges.astype(np.int64)
        if (rows == cols).any():
            raise ValueError("twin_edges: an edge is twinned with itself")
        same_row = rows[1:] == rows[:-1]
        if (np.diff(cols)[same_row] <= 0).any():
            raise ValueError("twin_edges: each row must be strictly ascending")
        if not np.array_equal(rows * m + cols, np.sort(cols * m + rows)):
            raise ValueError("twin_edges: twins must be symmetric")

        brand_of = np.repeat(np.arange(len(self.brand_indptr) - 1), np.diff(self.brand_indptr))
        same_brand = brand_of[1:] == brand_of[:-1]
        if (np.diff(self.brand_nodes.astype(np.int64))[same_brand] <= 0).any():
            raise ValueError("brand_nodes: each brand's nodes must be strictly ascending")

        node, e_in, e_out = self.trans_node, self.trans_in, self.trans_out
        d_node, d_in, d_out = np.diff(node), np.diff(e_in), np.diff(e_out)
        ascending = (d_node > 0) | ((d_node == 0) & ((d_in > 0) | ((d_in == 0) & (d_out > 0))))
        if not ascending.all():
            raise ValueError("trans_node, trans_in, trans_out: rows must be strictly ascending")
        if not (
            np.array_equal(self.edge_v[e_in], node) and np.array_equal(self.edge_u[e_out], node)
        ):
            raise ValueError("trans_in, trans_out: a transition's edges do not meet at its node")


def _check_indptr(name: str, indptr: NDArray[np.int64], n_values: int, min_run: int) -> None:
    if indptr[0] != 0 or indptr[-1] != n_values:
        raise ValueError(f"{name}: must run from 0 to {n_values}")
    if (np.diff(indptr) < min_run).any():
        raise ValueError(f"{name}: every run must hold at least {min_run} values")


def _check_range(name: str, values: NDArray[Any], lo: int, hi: int) -> None:
    if values.size and (values.min() < lo or values.max() >= hi):
        raise ValueError(f"{name}: values must lie in [{lo}, {hi})")
