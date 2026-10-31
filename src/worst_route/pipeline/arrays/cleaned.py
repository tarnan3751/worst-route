"""The arrays engine's cleanup stage on disk: the cleaned graph, `stages/cleanup/`.

The cleaned graph is a `stage_b.StageBGraph` as connectivity leaves it (`connectivity.keep_only`
and `without_no_turn` applied), whose edges and super-junctions name the stage A edges they are
made of. The stage holds both graphs' arrays, `a_<field>` for the stage A graph's and
`b_<field>` for the cleaned graph's, with the tag sets and the CRS in its tables, so the steps
after cleanup read the graph as cleanup left it, memory-mapped. `save_stage` writes it and the
report (`stages/cleanup.json`, as the networkx engine writes it); `load_stage` reads it back.
"""

import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from worst_route.pipeline import layout
from worst_route.pipeline.arrays.stage import read_stage, write_stage
from worst_route.pipeline.arrays.stage_a import StageAGraph
from worst_route.pipeline.arrays.stage_b import StageBGraph
from worst_route.pipeline.cleanup import CleanupReport

STAGE = "cleanup"  # the stage's directory under `stages/`


def save_stage(graph: StageBGraph, report: CleanupReport, build_dir: Path) -> None:
    """Write the graph as the `stages/cleanup/` stage and the report to `stages/cleanup.json`."""
    a = graph.stage_a
    arrays = {f"a_{f.name}": getattr(a, f.name) for f in fields(a)}
    del arrays["a_crs_epsg"], arrays["a_tagsets"]
    arrays |= {f"b_{f.name}": getattr(graph, f.name) for f in fields(graph) if f.name != "stage_a"}
    tables = {"crs_epsg": a.crs_epsg, "tagsets": a.tagsets}
    write_stage(layout.stage_file(build_dir, STAGE), arrays, tables)
    summary = layout.stage_file(build_dir, "cleanup.json")
    summary.write_text(json.dumps(asdict(report), indent=2) + "\n")


def load_stage(build_dir: Path) -> StageBGraph:
    """The `stages/cleanup/` stage, its arrays memory-mapped."""
    arrays, tables = read_stage(layout.stage_file(build_dir, STAGE))
    tagsets: list[Any] = tables["tagsets"]
    a = StageAGraph(
        crs_epsg=int(tables["crs_epsg"]),
        tagsets=[(c, tuple((k, v) for k, v in tags)) for c, tags in tagsets],
        **{name[2:]: array for name, array in arrays.items() if name.startswith("a_")},
    )
    b = {name[2:]: array for name, array in arrays.items() if name.startswith("b_")}
    return StageBGraph(stage_a=a, **b)
