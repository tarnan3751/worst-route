"""The arrays engine's tiers and build against the networkx engine's: the same tiers report and
the same build (every array, the strings, the graph_version) and geometry, from the enriched
graphs of every case of `test_arrays_enrich.py`; and the refusals alike."""

import dataclasses
from collections.abc import Callable
from pathlib import Path

import networkx as nx
import numpy as np
import pyarrow as pa
import pytest

from tests.pipeline.chain import FIXTURES, UTM_10N
from tests.pipeline.parity import assert_same_build, read_back
from tests.pipeline.test_arrays_enrich import CASES, every_poi
from worst_route.graph.geometry import GEOMETRY_SPECS
from worst_route.pipeline import save
from worst_route.pipeline.arrays import enrich as arrays_enrich
from worst_route.pipeline.arrays import tiers as arrays_tiers
from worst_route.pipeline.arrays.build import build_raw_arrays
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.cleanup import run_arrays_cleanup, run_cleanup
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.save import graph_data, graph_data_of, graph_geometry, graph_geometry_of
from worst_route.pipeline.tiers import run_tiers

META = {"region": "test", "build_id": "test", "crs_epsg": UTM_10N, "created": "", "params": {}}
META |= {"pipeline_version": "", "format": 4, "sources": []}


def assert_same_built(source: Path, pois: pa.Table) -> None:
    want, _ = run_cleanup(read_back(build_raw_graph(source, UTM_10N)[0]), allow_fragmented=True)
    graph, _ = run_arrays_cleanup(build_raw_arrays(source, UTM_10N)[0], allow_fragmented=True)
    run_enrich(want, pois)
    got, _ = arrays_enrich.run_enrich(graph, pois)
    want_tiers = run_tiers(want, allow_fragmented=True)
    got_tiers = arrays_tiers.run_tiers(graph, allow_fragmented=True)
    assert dataclasses.replace(got_tiers, seconds=0.0) == dataclasses.replace(
        want_tiers, seconds=0.0
    )
    tier_nodes = [t.node_ids for t in want_tiers.tiers]
    try:
        want_data = graph_data(want, tier_nodes, dict(META))
    except ValueError as error:  # a refusal (no turn, shape points, too many speeds)
        with pytest.raises(type(error), match=str(error).replace("(", r"\(").replace(")", r"\)")):
            graph_data_of(got, tier_nodes, dict(META))
        return
    got_data = graph_data_of(got, tier_nodes, dict(META))
    assert_same_build(want_data, got_data)
    want_shapes, got_shapes = graph_geometry(want, want_data), graph_geometry_of(got, got_data)
    for name in GEOMETRY_SPECS:
        a, b = getattr(want_shapes, name), getattr(got_shapes, name)
        assert a.dtype == b.dtype and a.tobytes() == b.tobytes(), name


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_build_is_the_same(tmp_path: Path, case: str) -> None:
    source = CASES[case](tmp_path)
    assert_same_built(source, every_poi(source))


def both_enriched(name: str) -> tuple[nx.MultiDiGraph, arrays_enrich.EnrichedGraph]:
    source = FIXTURES / f"{name}.osm"
    want, _ = run_cleanup(read_back(build_raw_graph(source, UTM_10N)[0]), allow_fragmented=True)
    run_enrich(want, every_poi(source))
    graph, _ = run_arrays_cleanup(build_raw_arrays(source, UTM_10N)[0], allow_fragmented=True)
    return want, arrays_enrich.run_enrich(graph, every_poi(source))[0]


def with_run(
    got: arrays_enrich.EnrichedGraph, e: int, run: list[int]
) -> arrays_enrich.EnrichedGraph:
    """The arrays engine's graph with edge `e`'s OSM nodes replaced, as its stage A edge's."""
    b, a = got.graph, got.graph.stage_a
    rows = np.split(a.osmnode_id, a.osmnode_indptr[1:-1])
    rows[int(b.edge_from[e])] = np.array(run, dtype=np.int64)
    indptr = np.concatenate(([0], np.cumsum([len(r) for r in rows]))).astype(np.int64)
    stage_a = dataclasses.replace(a, osmnode_indptr=indptr, osmnode_id=np.concatenate(rows))
    return dataclasses.replace(got, graph=dataclasses.replace(b, stage_a=stage_a))


def refusal(build: Callable[..., object], *args: object) -> str:
    with pytest.raises(ValueError) as refused:
        build(*args)
    return f"{type(refused.value).__name__}: {refused.value}"


def test_the_refusals_are_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    """A super-junction allowing no turn, OSM nodes that break the validator's proof (an end not
    a member, a member inside, a node twice inside, a node inside two roads) and more speeds than
    a build can index: both engines refuse them with the same error."""
    want, got = both_enriched("roundabout")
    tiers = [t.node_ids for t in run_tiers(want, allow_fragmented=True).tiers]
    (node,) = want.graph["superjunctions"]
    want.graph["superjunctions"][node]["transitions"] = ()
    rows = got.graph.trans_node != node
    b = got.graph
    b = dataclasses.replace(
        b, trans_node=b.trans_node[rows], trans_in=b.trans_in[rows], trans_out=b.trans_out[rows]
    )
    expected = refusal(graph_data, want, tiers, dict(META))
    assert "allows no turn" in expected
    assert refusal(graph_data_of, dataclasses.replace(got, graph=b), tiers, dict(META)) == expected

    want, got = both_enriched("grid")
    tiers = [t.node_ids for t in run_tiers(want, allow_fragmented=True).tiers]
    keys = sorted(want.edges(keys=True))
    unit = [want.edges[key]["unit"] for key in keys]
    one = next(k for k, key in enumerate(keys) if len(want.edges[key]["osm_nodes"]) > 2)
    other = next(k for k in range(len(keys)) if unit[k] != unit[one])
    ids, them = list(want.edges[keys[one]]["osm_nodes"]), list(want.edges[keys[other]]["osm_nodes"])
    ends = keys[one][:2]
    member = next(m for v, held in want.nodes(data="members") if v not in ends for m in held)
    for e, run in (
        (one, [ids[-1], *ids[1:-1], ids[-1]]),
        (one, [ids[0], member, *ids[1:]]),
        (one, [ids[0], ids[1], 10**12, ids[1], *ids[2:]]),
        (other, [them[0], ids[1], *them[1:]]),
    ):
        want, got = both_enriched("grid")
        want.edges[keys[e]]["osm_nodes"] = tuple(run)
        expected = refusal(graph_data, want, tiers, dict(META))
        assert expected.startswith("ShapePointError")
        assert refusal(graph_data_of, with_run(got, e, run), tiers, dict(META)) == expected

    want, got = both_enriched("grid")
    speeds = len(np.unique(got.edge_speed_kmh.astype(np.float32)))
    monkeypatch.setattr(save, "MAX_SPEEDS", speeds - 1)
    expected = refusal(graph_data, want, tiers, dict(META))
    assert refusal(graph_data_of, got, tiers, dict(META)) == expected
