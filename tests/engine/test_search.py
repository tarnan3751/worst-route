import numpy as np
import pytest

from worst_route.engine import search
from worst_route.engine.search import shortest_path, simple_paths
from worst_route.graph.backend import Path
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.testing import edge_id, make_graph

from .graphs import X0, Y0, lattice

STRAIGHT_AT_4 = [((3, 4), (4, 5)), ((5, 4), (4, 3)), ((1, 4), (4, 7)), ((7, 4), (4, 1))]


def nodes_of(path: Path | None) -> list[int] | None:
    return None if path is None else path[0].tolist()


def lengths(g: GraphData) -> np.ndarray:
    return g.edge_length_m.astype(np.float64)


def test_turn_edges_count_only_on_a_build_with_rows() -> None:
    for g, expected in [(lattice(3, 3), [4, 7]), (lattice(3, 3, transitions=STRAIGHT_AT_4), None)]:
        b, entry = PythonBackend(g), edge_id(g, 3, 4)
        path = shortest_path(b, 4, 7, lengths(g), entry_edge=entry)
        paths = simple_paths(b, 4, 7, entry_edge=entry, max_depth=3, max_paths=9)
        if expected is not None:  # no rows: every turn allowed, the entry edge ignored
            assert nodes_of(path) == expected
            assert [4, 7] in [p[0].tolist() for p in paths]
        else:  # arriving from 3, the junction allows only straight on, to 5
            assert nodes_of(path) == [4, 5, 8, 7]
            assert [p[0].tolist() for p in paths] == [[4, 5, 8, 7]]


def divided() -> GraphData:
    """S (0) to T (3) is shortest east on one carriageway, S → P, across, P → Q, and back west on
    the other, Q → T, the twin of S → P; the long way round is S V W X T.

        X ─────── T ← Q
        │           ↑
        │      S → P
        │      ↓
        W ──── V
    """
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 100, Y0 + 20), (X0, Y0 + 20)]
    xy += [(X0, Y0 - 100), (X0 - 100, Y0 - 100), (X0 - 100, Y0 + 20)]
    edges = [(0, 1, 100.0), (1, 2, 20.0), (2, 3, 100.0)]
    edges += [(0, 4, 100.0), (4, 5, 100.0), (5, 6, 120.0), (6, 3, 100.0)]
    return make_graph(xy, edges, twins=[((0, 1), (2, 3))])


def test_a_path_driving_both_carriageways_is_searched_again() -> None:
    g = divided()
    b = PythonBackend(g)
    assert nodes_of(shortest_path(b, 0, 3, lengths(g))) == [0, 1, 2, 3]
    assert nodes_of(shortest_path(b, 0, 3, lengths(g), avoid_twins=True)) == [0, 4, 5, 6, 3]
    banned = np.zeros(g.m, dtype=bool)
    banned[edge_id(g, 4, 5)] = True
    assert shortest_path(b, 0, 3, lengths(g), banned_edges=banned, avoid_twins=True) is None
    assert not banned[edge_id(g, 2, 3)]  # the caller's mask is left alone


def test_the_search_gives_up_after_its_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    g = divided()
    monkeypatch.setattr(search, "TWIN_RESEARCH_ROUNDS", 0)
    assert shortest_path(PythonBackend(g), 0, 3, lengths(g), avoid_twins=True) is None
