# Both engines on Portland's filtered roads, read through WORST_ROUTE_DATA: the same simplified
# and stage A graphs, the same cleaned graph and report, the same build. Run on demand with
# `uv run pytest -m data -k arrays` (about a minute, most of it the networkx engine's stage A);
# skipped when Portland has not been filtered. Nothing is written.
import dataclasses
import json
import pickle

import pyarrow.parquet as pq
import pytest

from tests.pipeline.parity import assert_same_build, assert_same_graph, read_back
from tests.pipeline.test_arrays import assert_same_simplified, assert_same_stage_a
from worst_route.config import REGIONS
from worst_route.graph.store import graph_version
from worst_route.pipeline import PIPELINE_VERSION, layout
from worst_route.pipeline.arrays.build import build_raw_arrays
from worst_route.pipeline.arrays.simplify import simplify as arrays_simplify
from worst_route.pipeline.arrays.stage_a import stage_a as arrays_stage_a
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.cleanup import run_cleanup
from worst_route.pipeline.consolidate import stage_a
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.save import graph_data
from worst_route.pipeline.simplify import simplify
from worst_route.pipeline.tiers import run_tiers

pytestmark = pytest.mark.data


def test_both_engines_build_portland_the_same() -> None:
    roads, pois = layout.roads_pbf("portland"), layout.pois_parquet("portland")
    if not roads.exists() or not pois.exists():
        pytest.skip(f"Portland is not filtered under {roads.parent}")
    crs = REGIONS["portland"].crs_epsg
    raw = read_back(build_raw_graph(roads, crs)[0])
    arrays, _ = build_raw_arrays(roads, crs)
    simplified, arrays_simplified = simplify(raw)[0], arrays_simplify(arrays)[0]
    assert_same_simplified(simplified, arrays_simplified)
    assert_same_stage_a(stage_a(simplified)[0], arrays_stage_a(arrays_simplified)[0])

    want, want_report = run_cleanup(raw)
    got, got_report = run_cleanup(arrays)
    assert_same_graph(want, got)  # and so the same stage file, byte for byte
    assert pickle.dumps(got, pickle.HIGHEST_PROTOCOL) == pickle.dumps(want, pickle.HIGHEST_PROTOCOL)
    assert dataclasses.replace(got_report, seconds={}) == dataclasses.replace(
        want_report, seconds={}
    )

    # The saved build's meta, when this pipeline version saved it, so its graph_version applies.
    meta = {"region": "portland", "build_id": "", "crs_epsg": crs, "created": "", "params": {}}
    meta |= {"pipeline_version": PIPELINE_VERSION, "sources": []}
    latest = layout.latest_file("portland")
    if latest.exists():
        saved = layout.build_dir("portland", latest.read_text().strip()) / "meta.json"
        if saved.exists():
            meta = json.loads(saved.read_text())
    table = pq.read_table(pois)
    builds = []
    for graph in (want, got):
        run_enrich(graph, table)
        builds.append(graph_data(graph, [t.node_ids for t in run_tiers(graph).tiers], meta))
    assert_same_build(*builds)
    if "graph_version" in meta and meta["pipeline_version"] == PIPELINE_VERSION:
        assert graph_version(builds[1]) == meta["graph_version"]
