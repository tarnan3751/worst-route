"""OSM turn restrictions: the turn tables cleanup makes of the filter's table, on both engines, and
the rows the build holds (`docs/reference/pipeline.md`, **Turn restrictions**). The fixture
`restrictions.osm` (its header has the network) holds one restriction of each kind the pipeline
applies or skips."""

import dataclasses
import pickle
from pathlib import Path

import networkx as nx
import numpy as np
import pyarrow as pa
import pytest

from tests.pipeline.chain import poi_table, restrictions_of, tier_nodes
from tests.pipeline.copies import META
from tests.pipeline.invariants import check_all, check_build
from tests.pipeline.osm import write_osm
from tests.pipeline.parity import assert_same_build, assert_same_graph, read_back
from worst_route.graph.backend import TURN_LEFT, TURN_STRAIGHT
from worst_route.pipeline.arrays import stage_b as arrays_stage_b
from worst_route.pipeline.arrays.build import build_raw_arrays
from worst_route.pipeline.arrays.simplify import simplify as arrays_simplify
from worst_route.pipeline.arrays.stage_a import stage_a as arrays_stage_a
from worst_route.pipeline.arrays.to_nx import cleanup_graph, stage_b_graph
from worst_route.pipeline.arrays.twins import find_twins as arrays_find_twins
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.carriageways import find_twins
from worst_route.pipeline.cleanup import run_arrays_cleanup, run_cleanup
from worst_route.pipeline.consolidate import stage_a, stage_b
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.restrictions import RestrictionStats, Rule, junction_table
from worst_route.pipeline.save import EmptyTransitionsError, graph_data
from worst_route.pipeline.simplify import simplify

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
FIXTURE = FIXTURES / "restrictions.osm"
UTM_10N = 32610

type Pair = tuple[tuple[int, int], tuple[int, int]]


@pytest.fixture(scope="module")
def table(tmp_path_factory: pytest.TempPathFactory) -> pa.Table:
    return restrictions_of(FIXTURE, tmp_path_factory.mktemp("restrictions"))


def ends(graph: nx.MultiDiGraph, edge: tuple[int, int, int]) -> tuple[int, int]:
    """An edge named by the first and last OSM node it runs over."""
    nodes = graph.edges[edge]["osm_nodes"]
    return nodes[0], nodes[-1]


def absent(graph: nx.MultiDiGraph) -> dict[tuple[int, ...], set[Pair]]:
    """For each node with a turn table, by its members: the (in, out) pairs it leaves out."""
    tables = {v: r["transitions"] for v, r in graph.graph["superjunctions"].items()}
    tables |= graph.graph.get("restricted", {})
    result = {}
    for node, transitions in tables.items():
        every = {
            (ends(graph, a), ends(graph, b))
            for a in graph.in_edges(node, keys=True)
            for b in graph.out_edges(node, keys=True)
        }
        allowed = {(ends(graph, a), ends(graph, b)) for a, b in transitions}
        assert allowed <= every
        result[graph.nodes[node]["members"]] = every - allowed
    return result


def both(source: Path, restrictions: pa.Table | None) -> nx.MultiDiGraph:
    """The cleaned graph, the same from both engines, byte for byte, with the same report."""
    raw = read_back(build_raw_graph(source, UTM_10N)[0])
    want, want_report = run_cleanup(raw, restrictions=restrictions)
    got, got_report = run_cleanup(build_raw_arrays(source, UTM_10N)[0], restrictions=restrictions)
    assert_same_graph(want, got)
    assert pickle.dumps(got, pickle.HIGHEST_PROTOCOL) == pickle.dumps(want, pickle.HIGHEST_PROTOCOL)
    assert dataclasses.replace(got_report, seconds={}) == dataclasses.replace(
        want_report, seconds={}
    )
    return want


def test_each_restriction_bans_the_turns_it_names(table: pa.Table) -> None:
    raw = build_raw_graph(FIXTURE, UTM_10N)[0]
    graph, report = run_cleanup(raw, restrictions=table)
    check_all(raw, graph, restrictions=table.to_pylist())
    assert report.restrictions == RestrictionStats(
        read=14,
        applied=11,
        unmatched=1,
        way_through_via=1,
        via_inside_edge=1,
        junctions=7,
        pairs_banned=22,
    )
    assert report.stage_b.restricted == 1 and report.connectivity.dropped_nodes == 0
    assert absent(graph) == {
        # 9001: no left from Alder St eastbound onto Birch Ave north
        (11,): {((10, 11), (11, 21))},
        # 9002: only straight on from Alder St eastbound; the U-turn back to 11, which no route
        # can make, stays in the table
        (12,): {((11, 12), (12, 22)), ((11, 12), (12, 23))},
        # 9003: no U-turn from the southbound carriageway to the northbound one where they meet
        (31,): {((37, 31), (31, 33))},
        # 9004: no left from the northbound carriageway onto the median the cluster absorbed, so
        # neither Dogwood St west nor the southbound carriageway from it
        (33, 37): {((31, 33), (37, 13)), ((31, 33), (37, 31))},
        # Lamb Rd passes through 61, so the turn tells its two edges apart: 9016, only the right
        # (west) from Generation Rd; 9017, no left onto Generation Rd from Lamb Rd eastbound,
        # westbound's right turn there allowed; 9018's no_entry names no turn and is skipped
        (61,): {((20, 61), (61, 62)), ((60, 61), (61, 20))},
        # Mill Rd and Pine St both pass through 71: each rule concerns the pairs making its turn.
        # 9019, no left from Mill Rd onto Pine St: west to north and east to south; 9020, no U-turn
        # on Mill Rd: only its U-turns back, which no route makes and the table keeps; 9021, only
        # left from Pine St onto Mill Rd: from the south only west, from the north only east
        (71,): {
            ((70, 71), (71, 73)),
            ((72, 71), (71, 74)),
            ((74, 71), (71, 72)),
            ((74, 71), (71, 73)),
            ((73, 71), (71, 70)),
            ((73, 71), (71, 74)),
        },
        # 9006: no left from Hill St north onto Grand Ave west, the only way there; 9005's right
        # turn from Grand Ave east onto Hill St south is still made by the slip lane
        (51, 52, 54, 56): {((57, 54), (51, 50))},
    }


def test_a_way_through_the_via_is_told_apart_by_the_turn() -> None:
    # Lamb Rd (way 10) arrives at via 2 from the west, heading east (90), and from the east
    # (270); Generation Rd (11) leaves it south (180).
    ins = [("west", 2, 10, 90), ("east", 2, 10, 270)]
    outs = [("south", 2, 11, 180)]

    def rule(turn: int | None) -> dict[int, list[Rule]]:
        return {2: [Rule(1, False, turn, frozenset({10}), frozenset({11}))]}

    stats = RestrictionStats()
    # a left onto Generation Rd is from the east: only that turn is banned
    assert junction_table(ins, outs, [], rule(TURN_LEFT), stats) == {("west", "south")}
    # neither edge goes straight on to it, and a no_entry names no turn: both skipped
    assert junction_table(ins, outs, [], rule(TURN_STRAIGHT), stats) is None
    assert junction_table(ins, outs, [], rule(None), stats) is None
    assert (stats.applied, stats.way_through_via, stats.pairs_banned) == (1, 2, 1)


def test_both_engines_make_the_same_tables(table: pa.Table) -> None:
    graph = both(FIXTURE, table)
    assert len(graph.graph["restricted"]) == 6


def test_without_the_table_every_turn_is_allowed() -> None:
    graph = both(FIXTURE, None)
    assert "restricted" not in graph.graph
    assert absent(graph) == {(51, 52, 54, 56): set()}


def stage_a_turns(graph: nx.MultiDiGraph) -> dict[int, set[tuple[object, object]]]:
    """A table at every node allowing every pair: it bans nothing."""
    return {
        node: {
            (a, b)
            for a in graph.in_edges(node, keys=True)
            for b in graph.out_edges(node, keys=True)
        }
        for node in graph
    }


@pytest.mark.parametrize("name", sorted(path.stem for path in FIXTURES.glob("*.osm")))
def test_a_table_that_bans_nothing_changes_nothing(name: str) -> None:
    """Stage B with a table allowing every pair at every node walks every super-junction edge by
    edge, for its passages and the twins it carries, and makes the graph it makes without one,
    byte for byte: the walk is the reachability it replaces where a table applies."""
    source = FIXTURES / f"{name}.osm"
    merged = stage_a(simplify(read_back(build_raw_graph(source, UTM_10N)[0]))[0])[0]
    find_twins(merged)
    plain, plain_stats = stage_b(merged)
    walked, walked_stats = stage_b(merged, stage_a_turns(merged))
    assert walked_stats == dataclasses.replace(plain_stats, restricted=walked_stats.restricted)
    assert pickle.dumps(walked) == pickle.dumps(plain)

    got_a = arrays_stage_a(arrays_simplify(build_raw_arrays(source, UTM_10N)[0])[0])[0]
    twins = arrays_find_twins(got_a)[:2]
    edges_in = {v: np.flatnonzero(got_a.edge_v == v).tolist() for v in range(len(got_a.node_x))}
    edges_out = {v: np.flatnonzero(got_a.edge_u == v).tolist() for v in range(len(got_a.node_x))}
    every = {v: {(a, b) for a in edges_in[v] for b in edges_out[v]} for v in edges_in}
    arrays_plain = arrays_stage_b.stage_b(got_a, twins)[0]
    arrays_walked = arrays_stage_b.stage_b(got_a, twins, every)[0]
    assert pickle.dumps(stage_b_graph(arrays_walked)) == pickle.dumps(stage_b_graph(arrays_plain))


def slip_beside_a_carriageway(tmp_path: Path, *, restricted: bool) -> Path:
    """Main Blvd, divided (northbound 2-4-5-7 at x 12, southbound 7-2 at x -12, one edge beside
    it), and a slip lane (17) from the northbound carriageway at 4 to Hill St, one way east from
    the carriageway at 5 through 20, where the slip lane joins it; stage B makes 4, 5 and 20 one
    super-junction. From the carriageway to Hill St east two ways lead through it: the slip lane,
    and the carriageway on to 5 and Hill St. With `restricted`, a no_straight_on from the slip lane
    onto Hill St east at 20 leaves the second only, which drives the carriageway stretch 4-5, a
    twin of the southbound edge. (Were Hill St two-way, the slip lane, Hill St west and a U-turn
    at 5 would still lead there without the stretch.)"""
    main = {"highway": "primary", "name": "Main Blvd"}
    hill = {"highway": "residential", "name": "Hill St", "oneway": "yes"}
    back = {"highway": "residential", "name": "Back Rd"}
    relations = [
        (
            9100,
            [("w", 17, "from"), ("n", 20, "via"), ("w", 16, "to")],
            {"type": "restriction", "restriction": "no_straight_on"},
        )
    ]
    return write_osm(
        tmp_path / "slip.osm",
        {
            1: (0, -1100),
            2: (0, -1000),
            3: (12, -900),
            4: (12, -100),
            5: (12, 0),
            6: (12, 900),
            7: (0, 1000),
            8: (-12, 900),
            9: (-12, -900),
            14: (0, 1100),
            20: (100, 0),
            21: (400, 0),
            22: (60, -60),
            23: (400, 1100),
            24: (400, -1100),
        },  # fmt: skip
        [
            (1, [1, 2], main),
            (10, [2, 3, 4], {**main, "oneway": "yes"}),
            (11, [4, 5], {**main, "oneway": "yes"}),
            (12, [5, 6, 7], {**main, "oneway": "yes"}),
            (13, [7, 8, 9, 2], {**main, "oneway": "yes"}),
            (14, [7, 14], main),
            (15, [5, 20], hill),
            (16, [20, 21], hill),
            (17, [4, 22, 20], {"highway": "tertiary_link", "oneway": "yes"}),
            (18, [21, 23, 14], back),
            (19, [21, 24, 1], back),
        ],
        relations if restricted else [],
    )


@pytest.mark.parametrize("restricted", [False, True], ids=["free", "restricted"])
def test_a_restriction_that_leaves_one_internal_path_carries_its_twin(
    tmp_path: Path, restricted: bool
) -> None:
    source = slip_beside_a_carriageway(tmp_path, restricted=restricted)
    graph = both(source, restrictions_of(source, tmp_path))
    (record,) = graph.graph["superjunctions"].values()
    assert record["members"] == (4, 5, 20, 22)
    twins = {
        ends(graph, edge): {ends(graph, other) for other in others}
        for edge, others in graph.graph["twins"].items()
    }
    hill_east, southbound = (20, 21), (7, 2)
    # Main Blvd north to Hill St east is allowed either way; with the restriction its one
    # internal path drives the carriageway stretch 4-5, so Hill St east bans the stretch's twin.
    assert ((2, 4), hill_east) in {
        (ends(graph, a), ends(graph, b)) for a, b in record["transitions"]
    }
    assert (southbound in twins.get(hill_east, set())) is restricted
    assert (hill_east in twins[southbound]) is restricted


def test_the_build_holds_the_tables(table: pa.Table) -> None:
    for engine in ("networkx", "arrays"):
        raw = build_raw_graph(FIXTURE, UTM_10N)[0]
        if engine == "networkx":
            graph = run_cleanup(raw, restrictions=table)[0]
        else:
            graph = cleanup_graph(
                run_arrays_cleanup(build_raw_arrays(FIXTURE, UTM_10N)[0], restrictions=table)[0]
            )
        run_enrich(graph, poi_table([]))
        data = graph_data(graph, tier_nodes(graph), META)
        check_build(data)
        assert data.meta["counts"]["restricted"] == 6
        assert data.meta["counts"]["superjunctions"] == 1
        restricted = set(graph.graph["restricted"])
        rows = set(data.trans_node.tolist())
        assert rows == restricted | set(graph.graph["superjunctions"])
        # 15 + 14 + 3 + 14 + 7 + 10 at the restricted nodes and 8 of 9 in the super-junction
        transitions = 15 + 14 + 3 + 14 + 7 + 10 + 8
        assert data.meta["counts"]["transitions"] == len(data.trans_node) == transitions
        if engine == "networkx":
            first = data
        else:
            assert_same_build(first, data)


def test_save_refuses_a_restricted_junction_that_allows_no_turn(table: pa.Table) -> None:
    graph = run_cleanup(build_raw_graph(FIXTURE, UTM_10N)[0], restrictions=table)[0]
    run_enrich(graph, poi_table([]))
    node = next(iter(graph.graph["restricted"]))
    graph.graph["restricted"][node] = ()
    with pytest.raises(EmptyTransitionsError, match="restricted junction .* allows no turn"):
        graph_data(graph, tier_nodes(graph), META)
