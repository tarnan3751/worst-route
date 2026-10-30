import numpy as np
import pytest
from pyproj import Transformer

from worst_route.config import HighwayClass
from worst_route.graph.data import EdgeFlag
from worst_route.graph.testing import GRID_ORIGIN, Edge, edge_id, grid_graph, make_graph


def pairs(g) -> set[tuple[int, int]]:
    return set(zip(g.edge_u.tolist(), g.edge_v.tolist(), strict=True))


def test_grid_two_way_everywhere() -> None:
    g = grid_graph(3, 4, 50.0)
    assert (g.n, g.m) == (12, 2 * (3 * 3 + 4 * 2))
    x0, y0 = GRID_ORIGIN
    assert (g.node_x[6], g.node_y[6]) == (x0 + 2 * 50.0, y0 + 1 * 50.0)
    assert np.all(g.edge_length_m == 50.0)
    assert (5, 6) in pairs(g) and (6, 5) in pairs(g)
    assert (2, 6) in pairs(g) and (6, 2) in pairs(g)


def test_grid_direction_strings() -> None:
    g = grid_graph(3, 3, 100.0, oneway_rows="+-.", oneway_cols="+..")
    p = pairs(g)
    # row 0 east only, row 1 west only, row 2 both ways
    assert {(0, 1), (1, 2)} <= p and not {(1, 0), (2, 1)} & p
    assert {(4, 3), (5, 4)} <= p and not {(3, 4), (4, 5)} & p
    assert {(6, 7), (7, 6), (7, 8), (8, 7)} <= p
    # column 0 north only, columns 1 and 2 both ways
    assert {(0, 3), (3, 6)} <= p and not {(3, 0), (6, 3)} & p
    assert {(1, 4), (4, 1), (5, 8), (8, 5)} <= p
    assert g.m == 2 + 2 + 4 + 2 + 4 + 4
    with pytest.raises(ValueError, match="exactly one"):
        edge_id(g, 1, 0)


@pytest.mark.parametrize(
    ("rows", "cols"),
    [({"oneway_rows": "++"}, {}), ({}, {"oneway_cols": "+-x"}), ({"oneway_rows": "+-+-"}, {})],
)
def test_grid_rejects_bad_direction_strings(rows: dict[str, str], cols: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="oneway_"):
        grid_graph(3, 3, 100.0, **rows, **cols)


def test_bearings_from_coordinates() -> None:
    g = grid_graph(3, 3, 100.0)
    for head, bearing in [(7, 0), (5, 90), (1, 180), (3, 270)]:
        e = edge_id(g, 4, head)
        assert g.edge_bearing_out[e] == g.edge_bearing_in[e] == bearing


def test_edge_ids_sorted_and_ways_keep_input_order() -> None:
    g = make_graph([(0.0, 0.0), (30.0, 40.0)], [(1, 0, 7.0), (0, 1, 9.0), (0, 1, 8.0)])
    assert g.edge_u.tolist() == [0, 0, 1]
    assert g.edge_length_m.tolist() == [9.0, 8.0, 7.0]
    assert g.way_osm_ids.tolist() == [2, 3, 1]
    assert g.member_osm_ids.tolist() == [1, 2]
    assert g.osmnode_ids.tolist() == [1, 2, 1, 2, 2, 1]
    with pytest.raises(ValueError, match="2 edges match"):
        edge_id(g, 0, 1)
    assert edge_id(g, 1, 0) == 2


def test_edge_defaults() -> None:
    g = make_graph([(0.0, 0.0), (30.0, 40.0)], [Edge(0, 1), Edge(1, 0, 10.0, speed_kmh=36.0)])
    assert g.edge_length_m.tolist() == [50.0, 10.0]
    assert g.edge_speed_kmh.tolist() == [40.0, 36.0]
    assert g.edge_time_s.tolist() == pytest.approx([4.5, 1.0])
    assert np.all(g.edge_highway == HighwayClass.RESIDENTIAL)
    assert np.all(g.edge_tier == 4)
    assert g.edge_unit.tolist() == [0, 1]
    assert np.all(g.node_state == -1) and np.all(g.node_kind == 0)
    assert g.coords.shape == (4, 2) and g.coord_indptr.tolist() == [0, 2, 4]
    assert g.tier_edge_mask.tolist() == [[False] * 2] * 4 + [[True] * 2]
    assert g.tier_node_mask.tolist() == [[False] * 2] * 4 + [[True] * 2]


def test_classes_tiers_flags_and_strings() -> None:
    nodes = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]
    g = make_graph(
        nodes,
        [
            Edge(0, 1, highway=HighwayClass.MOTORWAY, ref="I 5", name="Baldock Freeway"),
            Edge(1, 2, highway=HighwayClass.PRIMARY_LINK, flags=EdgeFlag.BRIDGE),
            Edge(2, 1, name="Main Street", toll_booths=2, level_crossings=1),
        ],
    )
    assert g.edge_tier.tolist() == [0, 1, 4]
    assert g.edge_flags.tolist() == [0, EdgeFlag.BRIDGE | EdgeFlag.LINK, 0]
    assert g.strings.names == ["Baldock Freeway", "Main Street"]
    assert g.strings.refs == ["I 5"]
    assert g.strings.identities == ["I 5", "Main Street"]
    assert g.edge_name.tolist() == [0, -1, 1]
    assert g.edge_ref.tolist() == [0, -1, -1]
    assert g.edge_identity.tolist() == [0, -1, 1]
    assert g.edge_toll_booths.tolist() == [0, 0, 2]
    assert g.edge_level_crossings.tolist() == [0, 0, 1]
    assert g.tier_edge_mask[0].tolist() == [True, False, False]
    assert g.tier_node_mask[0].tolist() == [True, True, False]
    assert g.tier_node_mask[1].tolist() == [True, True, True]


def test_pass_through_from_topology() -> None:
    # A 2 × 2 square 0-1-3-2 of two-way streets, a two-way spur 1 ↔ 4 (a cul-de-sac), a node 5
    # with only an out-edge, and a self-loop on 2.
    nodes = [(0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0), (200.0, 0.0), (0.0, -100.0)]
    two_way = [(0, 1), (1, 3), (3, 2), (2, 0), (1, 4)]
    edges = [(a, b, 100.0) for a, b in two_way] + [(b, a, 100.0) for a, b in two_way]
    g = make_graph(nodes, [*edges, (5, 0, 100.0), (2, 2, 10.0)])
    assert g.node_pass_through.tolist() == [True, True, True, True, False, False]


def test_twins_closed_symmetrically_and_transitions_sorted() -> None:
    nodes = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0), (0.0, 20.0), (100.0, 20.0), (200.0, 20.0)]
    edges = [(0, 1, 100.0), (1, 2, 100.0), (5, 4, 100.0), (4, 3, 100.0), (1, 4, 20.0)]
    g = make_graph(
        nodes,
        edges,
        twins=[((0, 1), (4, 3)), ((5, 4), (1, 2)), ((0, 1), (5, 4))],
        transitions=[((5, 4), (4, 3)), ((1, 4), (4, 3)), ((0, 1), (1, 4)), ((0, 1), (1, 2))],
    )
    e01, e12, e14 = edge_id(g, 0, 1), edge_id(g, 1, 2), edge_id(g, 1, 4)
    e43, e54 = edge_id(g, 4, 3), edge_id(g, 5, 4)

    def twins_of(e: int) -> list[int]:
        return g.twin_edges[g.twin_indptr[e] : g.twin_indptr[e + 1]].tolist()

    assert twins_of(e01) == sorted([e43, e54])
    assert twins_of(e43) == [e01]
    assert twins_of(e54) == sorted([e01, e12])
    assert twins_of(e12) == [e54]
    assert twins_of(e14) == []
    rows = list(zip(g.trans_node.tolist(), g.trans_in.tolist(), g.trans_out.tolist(), strict=True))
    assert rows == sorted([(1, e01, e12), (1, e01, e14), (4, e14, e43), (4, e54, e43)])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"twins": [((0, 1), (1, 0))]}, "edge \\(0, 1\\): 2 edges match"),
        ({"twins": [((1, 0), (0, 2))]}, "edge \\(0, 2\\): 0 edges match"),
        ({"transitions": [((1, 0), (1, 0))]}, "do not meet at its node"),
    ],
)
def test_twins_and_transitions_name_one_edge(kwargs: dict, message: str) -> None:
    edges = [(0, 1, 10.0), (0, 1, 12.0), (1, 0, 10.0)]
    with pytest.raises(ValueError, match=message):
        make_graph([(0.0, 0.0), (10.0, 0.0)], edges, **kwargs)


def test_lonlat_inverse_projection_or_given() -> None:
    g = grid_graph(2, 2, 100.0)
    to_xy = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
    x, y = to_xy.transform(g.node_lon, g.node_lat)
    assert np.allclose(x, g.node_x, atol=1e-6) and np.allclose(y, g.node_y, atol=1e-6)
    assert -122.7 < g.node_lon[0] < -122.6 and 45.5 < g.node_lat[0] < 45.6
    given = make_graph(
        [(0.0, 0.0), (1.0, 0.0)], [(0, 1, 1.0)], lonlat=[(-122.0, 45.0), (-121.0, 46.0)]
    )
    assert given.node_lon.tolist() == [-122.0, -121.0]
    assert given.coords.tolist() == [[-122.0, 45.0], [-121.0, 46.0]]


def test_arrays_are_read_only() -> None:
    g = grid_graph(2, 2, 100.0)
    with pytest.raises(ValueError, match="read-only"):
        g.edge_length_m[0] = 1.0
    with pytest.raises(ValueError, match="read-only"):
        g.tier_node_mask[0, 0] = True
