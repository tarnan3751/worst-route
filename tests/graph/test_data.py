from collections.abc import Callable
from dataclasses import fields, replace
from typing import Any

import numpy as np
import pytest

from worst_route.config import TIERS, HighwayClass
from worst_route.graph.data import ARRAY_SPECS, GraphData
from worst_route.graph.testing import make_graph

# A square of two-way streets: edge ids after the (u, v) sort are
# 0: 0→1, 1: 0→3, 2: 1→0, 3: 1→2, 4: 2→1, 5: 2→3, 6: 3→0, 7: 3→2.
# Two brands: 0 Acme at nodes 0 and 2, 1 Zed at nodes 1, 2 and 3.
SQUARE = make_graph(
    [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)],
    [(a, b, 100.0) for a, b in [(0, 1), (1, 2), (2, 3), (3, 0)]]
    + [(b, a, 100.0) for a, b in [(0, 1), (1, 2), (2, 3), (3, 0)]],
    twins=[((0, 1), (2, 3)), ((1, 0), (3, 2))],
    transitions=[((2, 1), (1, 0)), ((0, 1), (1, 2))],
    brands={"Zed": [3, 1, 2], "Acme": [2, 0]},
)


def edited(name: str, edit: Callable[[np.ndarray], Any]) -> Callable[[GraphData], GraphData]:
    """A mutation that copies one array, applies `edit` to the copy, and swaps it in."""

    def mutate(g: GraphData) -> GraphData:
        a = getattr(g, name).copy()
        result = edit(a)
        return replace(g, **{name: a if result is None else result})

    return mutate


def setitem(index: Any, value: Any) -> Callable[[np.ndarray], None]:
    def edit(a: np.ndarray) -> None:
        a[index] = value

    return edit


def with_params(g: GraphData, params: Any) -> GraphData:
    return replace(g, meta={**g.meta, "params": params})


def with_brand_strings(g: GraphData, brands: list[str], wikidata: list[str | None]) -> GraphData:
    return replace(g, strings=replace(g.strings, brands=brands, brand_wikidata=wikidata))


MALFORMED: list[tuple[str, Callable[[GraphData], GraphData], str]] = [
    ("not an array", lambda g: replace(g, node_x=g.node_x.tolist()), "node_x: not a NumPy"),
    ("dtype", edited("edge_u", lambda a: a.astype(np.int64)), "edge_u: dtype"),
    ("ndim", edited("tier_node_mask", lambda a: a.ravel()), "tier_node_mask: shape"),
    ("length n", edited("node_kind", lambda a: a[:-1]), "node_kind: shape"),
    ("length m", edited("edge_tier", lambda a: a[:-1]), "edge_tier: shape"),
    ("tier count", edited("tier_node_mask", lambda a: a[:-1]), "tier_node_mask: shape"),
    ("transition rows", edited("trans_in", lambda a: a[:-1]), "trans_in: shape"),
    (
        "meta missing",
        lambda g: replace(g, meta={k: v for k, v in g.meta.items() if k != "sources"}),
        "meta: missing keys",
    ),
    ("counts", lambda g: replace(g, meta={**g.meta, "counts": {"nodes": 4}}), "meta: counts"),
    ("format", lambda g: replace(g, meta={**g.meta, "format": 3}), "meta: format 3"),
    (
        "too many speeds",
        lambda g: replace(g, speed_kmh=np.arange(1, 258, dtype=np.float32)),
        "speed_kmh: 257 speeds",
    ),
    ("params int keys", lambda g: with_params(g, {2: "a", 10: "b"}), "meta: params"),
    ("params mixed keys", lambda g: with_params(g, {1: "a", "b": 2}), "meta: params"),
    ("params tuple", lambda g: with_params(g, {"bbox": (1.0, 2.0)}), "meta: params"),
    ("params object", lambda g: with_params(g, {"region": object()}), "meta: params"),
    ("params nan", lambda g: with_params(g, {"tolerance_m": float("nan")}), "meta: params"),
    ("duplicate member", edited("member_osm_ids", setitem(1, 1)), "member_osm_ids"),
    ("indptr start", edited("member_indptr", setitem(0, 1)), "member_indptr: must run"),
    (
        "indptr end",
        lambda g: replace(g, member_osm_ids=np.append(g.member_osm_ids, 99)),
        "member_indptr: must run",
    ),
    ("indptr order", edited("twin_indptr", setitem(1, 3)), "twin_indptr"),
    ("empty member run", edited("member_indptr", setitem(1, 0)), "member_indptr: every run"),
    ("node kind", edited("node_kind", setitem(0, 4)), "node_kind"),
    ("edge_v range", edited("edge_v", setitem(7, 4)), "edge_v"),
    ("edge_u range", edited("edge_u", setitem(0, -1)), "edge_u"),
    ("highway", edited("edge_highway", setitem(0, len(HighwayClass))), "edge_highway"),
    ("tier", edited("edge_tier", setitem(0, len(TIERS))), "edge_tier"),
    ("name index", edited("edge_name", setitem(0, 0)), "edge_name"),
    ("ref index", edited("edge_ref", setitem(0, -2)), "edge_ref"),
    ("identity index", edited("edge_identity", setitem(0, 0)), "edge_identity"),
    ("unit", edited("edge_unit", setitem(0, 8)), "edge_unit"),
    ("flags", edited("edge_flags", setitem(0, 32)), "edge_flags"),
    ("bearing", edited("edge_bearing_in", setitem(0, 360)), "edge_bearing_in"),
    ("bearing sign", edited("edge_bearing_out", setitem(0, -1)), "edge_bearing_out"),
    ("negative length", edited("edge_length_m", setitem(0, -1.0)), "edge_length_m"),
    ("nan time", edited("edge_time_s", setitem(0, np.nan)), "edge_time_s"),
    ("speed index", edited("edge_speed", setitem(0, 1)), "edge_speed"),
    ("speed zero", edited("speed_kmh", setitem(0, 0.0)), "speed_kmh: speeds must"),
    (
        "speed order",
        lambda g: replace(g, speed_kmh=np.array([40.0, 30.0], dtype=np.float32)),
        "speed_kmh: speeds must",
    ),
    ("twin range", edited("twin_edges", setitem(0, 8)), "twin_edges"),
    ("transition node", edited("trans_node", setitem(0, 4)), "trans_node"),
    ("transition edge", edited("trans_out", setitem(0, 8)), "trans_out"),
    (
        "edge order",
        lambda g: replace(g, edge_u=g.edge_u[::-1].copy(), edge_v=g.edge_v[::-1].copy()),
        "sorted by",
    ),
    ("out degree", edited("out_indptr", setitem(1, 1)), "out_indptr"),
    ("out order", edited("out_edges", setitem([0, 1], [1, 0])), "out_edges"),
    ("in degree", edited("in_indptr", setitem(1, 1)), "in_indptr"),
    ("in order", edited("in_edges", setitem([0, 1], [6, 2])), "in_edges"),
    ("twin self", edited("twin_edges", setitem(0, 0)), "twinned with itself"),
    ("twin asymmetric", edited("twin_edges", setitem(0, 6)), "symmetric"),
    (
        "twin row order",
        lambda g: replace(
            g,
            twin_indptr=np.array([0, 2, 2, 2, 2, 2, 4, 4, 4], dtype=np.int64),
            twin_edges=np.array([5, 5, 0, 0], dtype=np.int32),
        ),
        "strictly ascending",
    ),
    (
        "transition order",
        lambda g: replace(
            g,
            trans_node=g.trans_node[::-1].copy(),
            trans_in=g.trans_in[::-1].copy(),
            trans_out=g.trans_out[::-1].copy(),
        ),
        "rows must be strictly ascending",
    ),
    (
        "transition duplicate",
        lambda g: replace(
            g,
            trans_in=np.array([0, 0], dtype=np.int32),
            trans_out=np.array([3, 3], dtype=np.int32),
        ),
        "rows must be strictly ascending",
    ),
    ("transition out order", edited("trans_in", setitem(1, 0)), "rows must be strictly"),
    ("transition elsewhere", edited("trans_out", setitem(0, 0)), "do not meet at its node"),
    ("ring at a plain node", edited("node_roundabouts", setitem(0, 1)), "node_roundabouts"),
    ("brand count", lambda g: with_brand_strings(g, ["Acme"], [None]), "brand_indptr: shape"),
    ("brand wikidata", lambda g: with_brand_strings(g, ["Acme", "Zed"], [None]), "brand_wikidata"),
    ("brand rows", edited("brand_locations", lambda a: a[:-1]), "brand_locations: shape"),
    (
        "brand run end",
        lambda g: replace(
            g, brand_nodes=g.brand_nodes[:-1], brand_locations=g.brand_locations[:-1]
        ),
        "brand_indptr: must run",
    ),
    ("brand without a node", edited("brand_indptr", setitem(1, 0)), "brand_indptr: every run"),
    ("brand node range", edited("brand_nodes", setitem(0, 4)), "brand_nodes: values"),
    ("brand node order", edited("brand_nodes", setitem([2, 3], [2, 1])), "strictly ascending"),
    ("brand node twice", edited("brand_nodes", setitem(3, 1)), "strictly ascending"),
    ("brand location", edited("brand_locations", setitem(0, 0)), "brand_locations: values"),
]


def test_square_is_as_documented() -> None:
    assert SQUARE.strings.brands == ["Acme", "Zed"]
    assert SQUARE.brand_indptr.tolist() == [0, 2, 5]
    assert SQUARE.brand_nodes.tolist() == [0, 2, 1, 2, 3]
    assert SQUARE.nodes_of_brand(1).tolist() == [1, 2, 3]
    pairs = list(zip(SQUARE.edge_u.tolist(), SQUARE.edge_v.tolist(), strict=True))
    assert pairs == [(0, 1), (0, 3), (1, 0), (1, 2), (2, 1), (2, 3), (3, 0), (3, 2)]
    assert SQUARE.twin_indptr.tolist() == [0, 1, 1, 2, 2, 2, 3, 3, 4]
    assert SQUARE.twin_edges.tolist() == [5, 7, 0, 2]
    assert SQUARE.trans_node.tolist() == [1, 1]
    assert SQUARE.trans_in.tolist() == [0, 4]
    assert SQUARE.trans_out.tolist() == [3, 2]


def test_fields_follow_array_specs() -> None:
    names = [f.name for f in fields(GraphData)]
    assert names == [*ARRAY_SPECS, "strings", "meta"]


def test_tiers_nest_up_to_every_class() -> None:
    assert len(TIERS) == 5
    assert TIERS[0] == {
        HighwayClass.MOTORWAY,
        HighwayClass.MOTORWAY_LINK,
        HighwayClass.TRUNK,
        HighwayClass.TRUNK_LINK,
    }
    for smaller, larger in zip(TIERS, TIERS[1:], strict=False):
        assert smaller < larger
    assert TIERS[1] - TIERS[0] == {HighwayClass.PRIMARY, HighwayClass.PRIMARY_LINK}
    assert TIERS[3] - TIERS[2] == {HighwayClass.TERTIARY, HighwayClass.TERTIARY_LINK}
    assert TIERS[4] == set(HighwayClass)


def test_valid_graph_passes() -> None:
    SQUARE.validate()
    SQUARE.validate(deep=False)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [pytest.param(mutate, message, id=case) for case, mutate, message in MALFORMED],
)
def test_validate_rejects(mutate: Callable[[GraphData], GraphData], message: str) -> None:
    broken = mutate(SQUARE)
    with pytest.raises(ValueError, match=message):
        broken.validate()


def test_json_native_params_pass() -> None:
    params = {"tolerance_m": {"arterial": 15, "other": 8}, "max_length": 60.0, "keep": [1, "a"]}
    with_params(SQUARE, params).validate()
    with_params(SQUARE, {}).validate()


def test_shallow_validation_skips_contents() -> None:
    broken = edited("out_edges", setitem([0, 1], [1, 0]))(SQUARE)
    broken.validate(deep=False)
    with pytest.raises(ValueError, match="out_edges"):
        broken.validate()
    with pytest.raises(ValueError, match="edge_u: dtype"):
        edited("edge_u", lambda a: a.astype(np.int64))(SQUARE).validate(deep=False)


def test_empty_graph_is_valid() -> None:
    empty = make_graph([], [])
    assert (empty.n, empty.m) == (0, 0)
    empty.validate()
