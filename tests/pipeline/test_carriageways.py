from pathlib import Path

import networkx as nx
import pytest

from tests.pipeline.chain import poi_table
from tests.pipeline.invariants import check_twins
from tests.pipeline.osm import write_osm
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.carriageways import TwinStats, find_twins
from worst_route.pipeline.consolidate import StageBStats, stage_a, stage_b
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.simplify import simplify

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
UTM_10N = 32610

type Runs = dict[tuple[int, ...], set[tuple[int, ...]]]


def twinned(source: Path) -> tuple[nx.MultiDiGraph, TwinStats, StageBStats]:
    """Twins found on the stage A graph, and the stage B graph they are carried into."""
    graph = stage_a(simplify(build_raw_graph(source, UTM_10N)[0])[0])[0]
    stats = find_twins(graph)
    check_twins(graph)
    final, carried = stage_b(graph)
    check_twins(final)
    return final, stats, carried


def runs(graph: nx.MultiDiGraph) -> Runs:
    """The twins table, each edge named by its OSM node run."""
    name = {(u, v, k): d["osm_nodes"] for u, v, k, d in graph.edges(keys=True, data=True)}
    return {name[e]: {name[f] for f in others} for e, others in graph.graph["twins"].items()}


def symmetric(pairs: list[tuple[tuple[int, ...], tuple[int, ...]]]) -> Runs:
    table: Runs = {}
    for a, b in pairs:
        table.setdefault(a, set()).add(b)
        table.setdefault(b, set()).add(a)
    return table


def edited(tmp_path: Path, name: str, *changes: tuple[str, str]) -> Path:
    text = (FIXTURES / f"{name}.osm").read_text()
    for old, new in changes:
        assert old in text
        text = text.replace(old, new)
    path = tmp_path / f"{name}.osm"
    path.write_text(text)
    return path


EASTBOUND_WEST = (70, 71, 72, 31)
EASTBOUND_TO_CROSSING = (31, 73, 50)
EASTBOUND_FROM_CROSSING = (51, 74, 32)
EASTBOUND_EAST = (32, 75, 76, 80)
WESTBOUND_EAST = (80, 81, 82, 83, 84, 53)
WESTBOUND_FROM_CROSSING = (52, 33)
WESTBOUND_WEST = (33, 86, 87, 88, 70)


def test_divided_twins() -> None:
    graph, stats, _ = twinned(FIXTURES / "divided.osm")
    assert stats == TwinStats(candidates=7, paired=7, pairs=5, motorway_trunk_m=0.0, coverage=None)
    assert runs(graph) == symmetric(
        [
            (EASTBOUND_WEST, WESTBOUND_WEST),
            (EASTBOUND_TO_CROSSING, WESTBOUND_WEST),
            (EASTBOUND_TO_CROSSING, WESTBOUND_FROM_CROSSING),
            (EASTBOUND_FROM_CROSSING, WESTBOUND_EAST),
            (EASTBOUND_EAST, WESTBOUND_EAST),
        ]
    )
    # Leaving the crossing east on one side and west on the other is not one stretch of road.
    assert WESTBOUND_FROM_CROSSING not in runs(graph)[EASTBOUND_FROM_CROSSING]


def test_different_refs_do_not_pair(tmp_path: Path) -> None:
    graph, stats, _ = twinned(edited(tmp_path, "divided", ('v="OR-99W"', 'v="OR 10"')))
    assert (stats.candidates, stats.paired) == (7, 0)
    assert graph.graph["twins"] == {}


def test_names_pair_without_refs(tmp_path: Path) -> None:
    source = edited(tmp_path, "divided", ('<tag k="ref" v="OR 99W"/>', ""), ('v="OR-99W"', 'v=""'))
    _, stats, _ = twinned(source)
    assert (stats.candidates, stats.pairs) == (7, 5)  # SW Barbur Blvd on both sides


def test_unnamed_primaries_are_not_candidates(tmp_path: Path) -> None:
    source = edited(
        tmp_path,
        "divided",
        ('<tag k="ref" v="OR 99W"/>', ""),
        ('<tag k="ref" v="OR-99W"/>', ""),
        ('<tag k="name" v="SW Barbur Blvd"/>', ""),
    )
    _, stats, _ = twinned(source)
    assert stats.candidates == 0


def test_diamond_twins() -> None:
    graph, stats, carried = twinned(FIXTURES / "diamond.osm")
    # Found on the stage A graph: both approaches with the opposite departures, and the two
    # mainline stretches between the gores, which stage B makes internal to one super-node.
    assert (stats.candidates, stats.paired, stats.pairs, stats.coverage) == (6, 6, 3, 1.0)
    assert stats.motorway_trunk_m > 0
    # Carried: the stretches inside the interchange pass once with it; the approaches from
    # north and south both end at the interchange but are not twins.
    assert carried.twin_pairs == 2
    assert runs(graph) == symmetric(
        [((100, 101, 102), (113, 114, 115)), ((103, 104, 105), (110, 111, 112))]
    )


def test_unsigned_motorways_pair_by_geometry(tmp_path: Path) -> None:
    source = edited(
        tmp_path, "diamond", ('<tag k="ref" v="I 5"/>', ""), ('<tag k="ref" v="I-5"/>', "")
    )
    graph, stats, carried = twinned(source)
    assert (stats.candidates, stats.pairs, stats.coverage) == (6, 3, 1.0)
    # Without refs the two sides share one identity, Main St, so they stay two super-nodes and
    # the stretches between the gores, internal to different ones, carry to both sides.
    assert len(graph.graph["superjunctions"]) == 2
    assert carried.twin_pairs == 4


def test_cloverleaf_twins() -> None:
    graph, stats, carried = twinned(FIXTURES / "cloverleaf.osm")
    # Each carriageway is an approach, three runs between gores and a departure.
    assert (stats.candidates, stats.paired, stats.pairs, stats.coverage) == (20, 20, 10, 1.0)
    assert carried.twin_pairs == 4
    assert runs(graph) == symmetric(
        [
            ((290, 291, 200), (213, 296, 297)),  # I 84 west of the interchange
            ((203, 292, 293), (294, 295, 210)),  # I 84 east
            ((280, 281, 220), (233, 286, 287)),  # I 205 south
            ((223, 282, 283), (284, 285, 230)),  # I 205 north
        ]
    )


def test_couplets_do_not_pair(tmp_path: Path) -> None:
    # One ref, two names: a one-way couplet a block apart, not a divided road.
    source = edited(
        tmp_path,
        "divided",
        (
            '<tag k="name" v="SW Barbur Blvd"/>\n    <tag k="ref" v="OR-99W"/>',
            '<tag k="name" v="SW Naito Pkwy"/>\n    <tag k="ref" v="OR-99W"/>',
        ),
    )
    graph, stats, _ = twinned(source)
    assert (stats.candidates, stats.paired) == (7, 0)
    assert graph.graph["twins"] == {}


def test_roundabout_rings_are_not_candidates(tmp_path: Path) -> None:
    source = edited(
        tmp_path,
        "roundabout",
        (
            '<tag k="highway" v="tertiary"/>',
            '<tag k="highway" v="primary"/><tag k="name" v="Ring Rd"/>',
        ),
    )
    _, stats, _ = twinned(source)
    assert stats.candidates == 0


def one_sided(tmp_path: Path) -> Path:
    """I-5 with ramps on its northbound side only, to Main St, which passes under both sides.

    Stage B makes the northbound stretch 2-5 internal; the southbound stretch 12-15 beside it
    stays outside, between two side streets.
    """
    motorway = {"highway": "motorway", "ref": "I 5", "oneway": "yes"}
    return write_osm(
        tmp_path / "one_sided.osm",
        {
            1: (15, -900),
            2: (15, -300),
            3: (15, -100),
            4: (15, 100),
            5: (15, 300),
            6: (15, 900),
            11: (-15, 900),
            12: (-15, 300),
            13: (-15, 100),
            14: (-15, -100),
            15: (-15, -300),
            16: (-15, -900),
            21: (-200, 300),
            22: (-200, -300),
            31: (-300, 0),
            32: (80, 0),
            33: (300, 0),
            41: (50, -150),
            42: (50, 150),
        },
        [
            (1, [1, 2, 3, 4, 5, 6], motorway),
            (2, [11, 12, 13, 14, 15, 16], motorway),
            (3, [12, 21], {"highway": "residential", "name": "Oak St"}),
            (4, [15, 22], {"highway": "residential", "name": "Elm St"}),
            (5, [31, 32, 33], {"highway": "secondary", "name": "Main St"}),
            (6, [2, 41, 32], {"highway": "motorway_link"}),
            (7, [32, 42, 5], {"highway": "motorway_link"}),
        ],
    )


def test_a_stretch_inside_an_interchange_keeps_its_twins(tmp_path: Path) -> None:
    graph, stats, carried = twinned(one_sided(tmp_path))
    assert stats.pairs == 3  # each southbound edge beside one northbound edge
    table = runs(graph)
    # The southbound stretch beside the internal one is twinned with the interchange's
    # northbound boundary edges, so going through it on I-5 north bans it, and with nothing
    # on Main St.
    assert table[12, 13, 14, 15] == {(1, 2), (5, 6)}
    assert table[1, 2] == {(15, 16), (12, 13, 14, 15)}
    assert table[5, 6] == {(11, 12), (12, 13, 14, 15)}
    assert not any(31 in run or 33 in run for run in table)
    assert carried.twin_pairs == 4


def offset_gores(tmp_path: Path) -> Path:
    """A diamond whose southbound gores (at +-250 m) lie inside the northbound ones (+-350 m)."""
    nodes = {
        1: (15, -900), 2: (15, -350), 3: (15, 350), 4: (15, 900),
        11: (-15, 900), 12: (-15, 250), 13: (-15, -250), 14: (-15, -900),
        31: (-300, 0), 32: (-80, 0), 33: (80, 0), 34: (300, 0),
        41: (50, -200), 42: (50, 200), 43: (-50, 150), 44: (-50, -150),
    }  # fmt: skip
    north = [100 + i for i in range(6)]  # shape points every 100 m between the gores
    nodes.update({n: (15, -250 + 100 * i) for i, n in enumerate(north)})
    south_in, south_out = [120, 121], [130, 131]
    nodes.update({120: (-15, 450), 121: (-15, 350), 130: (-15, -350), 131: (-15, -450)})
    motorway = {"highway": "motorway", "ref": "I 5", "oneway": "yes"}
    return write_osm(
        tmp_path / "offset.osm",
        nodes,
        [
            (1, [1, 2, *north, 3, 4], motorway),
            (2, [11, *south_in, 12, 13, *south_out, 14], motorway),
            (3, [31, 32, 33, 34], {"highway": "secondary", "name": "Main St"}),
            (4, [2, 41, 33], {"highway": "motorway_link"}),
            (5, [33, 42, 3], {"highway": "motorway_link"}),
            (6, [12, 43, 32], {"highway": "motorway_link"}),
            (7, [32, 44, 13], {"highway": "motorway_link"}),
        ],
    )


def test_carried_twins_keep_the_opposite_carriageway_open(tmp_path: Path) -> None:
    graph, _, _ = twinned(offset_gores(tmp_path))
    table = runs(graph)
    south_in, south_out = (11, 120, 121, 12), (13, 130, 131, 14)
    north_in, north_out = (1, 2), (3, 4)
    # The northbound stretch between the gores runs beside both southbound boundary edges; it
    # carries to the northbound boundary edges only, so driving straight through on I-5 south
    # stays possible.
    assert south_out not in table[south_in]
    assert table[south_in] >= {north_out}
    assert table[south_out] >= {north_in}


@pytest.mark.parametrize("name", ["refchange", "overpass", "roundabout", "grid", "oneway"])
def test_no_twins(name: str) -> None:
    graph, stats, _ = twinned(FIXTURES / f"{name}.osm")
    assert stats.paired == 0 and graph.graph["twins"] == {}


def corbett(tmp_path: Path, linked: bool) -> Path:
    """SW Macadam Ave divided, Corbett Ave jogging along its northbound side from P (2) to Q (5),
    and an interchange of two ramps there: P to Bancroft St and Hamilton St (or, `linked`,
    Bancroft St again) to Q. Unlinked, the only way through it from P to Q is the northbound
    stretch 2-5, whose twin 12-16 lies outside it."""
    one_way = {"highway": "primary", "name": "SW Macadam Ave", "oneway": "yes"}
    link = {"highway": "motorway_link"}
    second = "SW Bancroft St" if linked else "SW Hamilton St"
    ways = [
        (1, [1, 2, 3, 4, 5, 6], one_way),
        (2, [11, 12, 13, 14, 15, 16, 17], one_way),
        (3, [12, 21], {"highway": "residential", "name": "SW Lane St"}),
        (4, [16, 22], {"highway": "residential", "name": "SW Gibbs St"}),
        (5, [31, 2], {"highway": "residential", "name": "SW Corbett Ave"}),
        (6, [5, 32], {"highway": "residential", "name": "SW Corbett Ave"}),
        (7, [2, 42, 41], link),
        (8, [43, 44, 5], link),
        (9, [41, 45], {"highway": "residential", "name": "SW Bancroft St"}),
        (10, [43, 46], {"highway": "residential", "name": second}),
    ]
    if linked:
        ways.append((11, [41, 43], {"highway": "residential", "name": "SW Bancroft St"}))
    return write_osm(
        tmp_path / "corbett.osm",
        {
            1: (15, -600),
            2: (15, -150),
            3: (15, -50),
            4: (15, 50),
            5: (15, 150),
            6: (15, 600),
            11: (-15, 600),
            12: (-15, 200),
            13: (-15, 100),
            14: (-15, 0),
            15: (-15, -100),
            16: (-15, -200),
            17: (-15, -600),
            21: (-200, 200),
            22: (-200, -200),
            31: (300, -150),
            32: (300, 150),
            41: (250, -450),
            42: (100, -300),
            43: (250, 450),
            44: (100, 300),
            45: (400, -450),
            46: (400, 450),
        },
        ways,
    )


def test_a_transition_that_must_drive_a_twinned_stretch_bans_its_twin(tmp_path: Path) -> None:
    graph, _, carried = twinned(corbett(tmp_path, linked=False))
    # Coming in on Corbett at P and leaving on Corbett at Q drives the northbound stretch
    # inside the interchange, so both Corbett edges ban the southbound stretch beside it.
    assert runs(graph)[12, 13, 14, 15, 16] == {(1, 2), (5, 6), (31, 2), (5, 32)}
    assert carried.twin_transitions == 4  # into P (Macadam, Corbett) x out of Q (Macadam, Corbett)


def test_a_transition_with_another_way_through_bans_nothing_more(tmp_path: Path) -> None:
    graph, _, carried = twinned(corbett(tmp_path, linked=True))
    # Bancroft St joins the two ramps inside the interchange: P to Q need not drive 2-5.
    assert runs(graph)[12, 13, 14, 15, 16] == {(1, 2), (5, 6)}
    assert carried.twin_transitions == 0


def test_directional_words_do_not_make_names_differ(tmp_path: Path) -> None:
    source = edited(
        tmp_path,
        "divided",
        (
            '<tag k="name" v="SW Barbur Blvd"/>\n    <tag k="ref" v="OR-99W"/>',
            '<tag k="name" v="Barbur Blvd South"/>\n    <tag k="ref" v="OR-99W"/>',
        ),
    )
    _, stats, _ = twinned(source)
    assert (stats.candidates, stats.pairs) == (7, 5)


def test_motorway_names_are_not_compared(tmp_path: Path) -> None:
    source = edited(
        tmp_path,
        "diamond",
        ('<tag k="ref" v="I 5"/>', '<tag k="ref" v="I 5"/><tag k="name" v="Banfield Freeway"/>'),
        (
            '<tag k="ref" v="I-5"/>',
            '<tag k="ref" v="I-5"/><tag k="name" v="Columbia River Highway"/>',
        ),
    )
    _, stats, carried = twinned(source)
    assert (stats.pairs, carried.twin_pairs) == (3, 2)


def test_trunk_names_are_compared(tmp_path: Path) -> None:
    source = edited(
        tmp_path,
        "divided",
        ('v="primary"', 'v="trunk"'),
        (
            '<tag k="name" v="SW Barbur Blvd"/>\n    <tag k="ref" v="OR-99W"/>',
            '<tag k="name" v="SW Naito Pkwy"/>\n    <tag k="ref" v="OR-99W"/>',
        ),
    )
    _, stats, _ = twinned(source)
    assert (stats.candidates, stats.pairs) == (7, 0)


def test_a_way_curling_back_beside_itself_is_not_twinned(tmp_path: Path) -> None:
    secondary = {"highway": "secondary", "oneway": "yes"}
    source = write_osm(
        tmp_path / "hairpin.osm",
        {
            1: (0, 0),
            2: (150, 0),
            3: (200, 12),
            4: (150, 25),
            5: (0, 25),
            6: (300, 12),
            11: (0, 200),
            12: (300, 200),
            13: (300, 225),
            14: (0, 225),
        },
        [
            # One way, split at its apex by a side street: its two halves run 25 m apart in
            # opposite directions, but they are one road.
            (1, [1, 2, 3, 4, 5], {**secondary, "name": "Hairpin Rd"}),
            (2, [3, 6], {"highway": "residential", "name": "Side St"}),
            # Two ways with one ref: two carriageways.
            (3, [11, 12], {**secondary, "ref": "OR 10"}),
            (4, [13, 14], {**secondary, "ref": "OR 10"}),
        ],
    )
    graph, stats, _ = twinned(source)
    assert stats.pairs == 1
    assert runs(graph) == symmetric([((11, 12), (13, 14))])


def mapped_twice(tmp_path: Path) -> Path:
    """Mill Rd mapped twice, as two one-way ways over the same nodes (1, 2, 3) in opposite
    directions, between two side streets: one road, not two carriageways."""
    mill_rd = {"highway": "secondary", "name": "Mill Rd", "oneway": "yes"}
    return write_osm(
        tmp_path / "twice.osm",
        {1: (0, 0), 2: (150, 0), 3: (300, 0), 4: (0, -200), 5: (300, 200)},
        [
            (1, [1, 2, 3], mill_rd),
            (2, [3, 2, 1], mill_rd),
            (3, [4, 1], {"highway": "residential", "name": "Oak St"}),
            (4, [3, 5], {"highway": "residential", "name": "Elm St"}),
        ],
    )


def test_a_road_mapped_twice_is_one_road_not_twins(tmp_path: Path) -> None:
    graph, stats, _ = twinned(mapped_twice(tmp_path))
    assert stats == TwinStats(
        candidates=2, paired=0, pairs=0, same_road_pairs=1, motorway_trunk_m=0.0, coverage=None
    )
    assert graph.graph["twins"] == {}
    # Enrich makes the two edges one road unit, which the engine never drives twice.
    run_enrich(graph, poi_table([]))
    mill_rd = sorted(
        (u, v, k) for u, v, k, name in graph.edges(keys=True, data="name") if name == "Mill Rd"
    )
    assert sorted(graph.edges[e]["osm_nodes"] for e in mill_rd) == [(1, 2, 3), (3, 2, 1)]
    assert {graph.edges[e]["unit"] for e in mill_rd} == {mill_rd[0]}


def test_a_road_mapped_twice_beside_a_divided_road(tmp_path: Path) -> None:
    secondary = {"highway": "secondary", "oneway": "yes"}
    source = write_osm(
        tmp_path / "twice_beside.osm",
        {
            1: (0, 0),
            2: (150, 0),
            3: (300, 0),
            11: (0, 200),
            12: (300, 200),
            13: (300, 225),
            14: (0, 225),
        },
        [
            # One road mapped twice, over the same nodes in opposite directions.
            (1, [1, 2, 3], {**secondary, "name": "Mill Rd"}),
            (2, [3, 2, 1], {**secondary, "name": "Mill Rd"}),
            # Two ways with one ref: two carriageways.
            (3, [11, 12], {**secondary, "ref": "OR 10"}),
            (4, [13, 14], {**secondary, "ref": "OR 10"}),
        ],
    )
    graph, stats, _ = twinned(source)
    assert (stats.candidates, stats.pairs, stats.same_road_pairs) == (4, 1, 1)
    assert runs(graph) == symmetric([((11, 12), (13, 14))])


def cornell(tmp_path: Path) -> Path:
    """NW Cornell Rd divided east of M (2), its median ending at M inside an interchange that
    holds Joy Ave's two T-junctions, 8 on the westbound side and 4 on the eastbound side, and
    two ramps. From Joy north to Joy south the only way is out on the westbound carriageway, a
    U-turn at M and back on the eastbound one."""
    cornell_rd = {"highway": "primary", "name": "NW Cornell Rd"}
    one_way = {**cornell_rd, "oneway": "yes"}
    joy = {"highway": "residential", "name": "NW Joy Ave"}
    return write_osm(
        tmp_path / "cornell.osm",
        {
            1: (-400, 0),
            2: (-100, 0),
            3: (-50, -12),
            4: (0, -12),
            5: (200, -12),
            6: (400, 0),
            7: (200, 12),
            8: (0, 12),
            9: (-50, 12),
            10: (0, 200),
            11: (0, -200),
            12: (150, 150),
            13: (60, 80),
            14: (300, 150),
            15: (150, -150),
            16: (60, -80),
            17: (300, -150),
            18: (600, 0),
        },
        [
            (1, [1, 2], cornell_rd),
            (10, [6, 18], cornell_rd),  # undivided again east of 6
            (2, [2, 3, 4, 5, 6], one_way),  # eastbound
            (3, [6, 7, 8, 9, 2], one_way),  # westbound
            (4, [10, 8], joy),
            (5, [4, 11], joy),
            (6, [8, 13, 12], {"highway": "motorway_link"}),
            (7, [12, 14], {"highway": "residential", "name": "NW Ramp A St"}),
            (8, [15, 16, 4], {"highway": "motorway_link"}),
            (9, [15, 17], {"highway": "residential", "name": "NW Ramp B St"}),
        ],
    )


def test_a_passage_that_must_drive_both_carriageways_is_banned(tmp_path: Path) -> None:
    graph, _, carried = twinned(cornell(tmp_path))
    (node,) = graph.graph["superjunctions"]
    assert (2,) in graph.graph["superjunctions"][node]["parts"]  # the median's end is inside
    table = runs(graph)
    # Joy north to Joy south (or on east on Cornell) drives westbound to M and eastbound back.
    assert table[10, 8] >= {(4, 11), (4, 5, 6)}
    # Joy north to Cornell west stays on the westbound carriageway.
    assert (2, 1) not in table[10, 8]
    assert carried.twin_passages == 4  # from 8 (Joy, Cornell westbound) to 4 (Joy, eastbound)
