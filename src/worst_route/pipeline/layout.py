"""Where the pipeline's files live, all under `settings().data_dir` (`docs/reference/pipeline.md`).

Raw extracts are `raw/<state>-latest.osm.pbf`, each with the `.md5` file Geofabrik publishes
beside it; a region's filtered roads, POIs and turn restrictions are
`filtered/<region>.roads.osm.pbf`, `filtered/<region>.pois.parquet` and
`filtered/<region>.restrictions.parquet`. A build is `graphs/<region>/<build_id>/`, its intermediate
stages under `stages/` and its QA report under `qa/` (the build's own files, written by
`graph.store`, sit beside them), and `graphs/<region>/latest` holds the id of the region's
current build.
Paths are computed on every call, so a test that points `WORST_ROUTE_DATA` elsewhere and clears
the settings cache sees its own tree.

A build id is the first 12 hex digits of a sha256 over the filtered roads' md5, the region's
name, bbox and projected CRS, and `PIPELINE_VERSION`. A step that wrote `<output>` records its
inputs' md5s and its parameters in the sidecar `<output>.inputs.json`; the step is skipped while
its output exists and the sidecar matches what it would record now. `stale_stages` walks a
build's stages in order (`STAGE_CHAIN`) and names those whose recorded inputs no longer match
the files there, so a step never reads a stage made from inputs that have since changed.
The two engines' stages differ (`BUILD_STAGES`, `CLEANUP_STAGES`, `ENRICH_STAGES`): networkx's
pickles, the arrays engine's directories, each of which its `manifest.json` stands for in a
sidecar. A build holds one engine's cleanup stage, which `cleanup_engine` names and the steps
after cleanup follow; `build_stage` names the build stage its cleanup read.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from worst_route.config import DEFAULT_PIPELINE_ENGINE, PipelineEngine, Region, settings
from worst_route.pipeline import PIPELINE_VERSION


def raw_pbf(state: str) -> Path:
    """The state's Geofabrik extract."""
    return settings().data_dir / "raw" / f"{state}-latest.osm.pbf"


def raw_md5(state: str) -> Path:
    """The `.md5` file published with the state's extract."""
    pbf = raw_pbf(state)
    return pbf.with_name(f"{pbf.name}.md5")


def file_md5(path: Path) -> str:
    """The file's md5, as lowercase hex."""
    with path.open("rb") as f:
        return hashlib.file_digest(f, "md5").hexdigest()


def roads_pbf(region: str) -> Path:
    """The region's filtered roads."""
    return settings().data_dir / "filtered" / f"{region}.roads.osm.pbf"


def pois_parquet(region: str) -> Path:
    """The region's POI table."""
    return settings().data_dir / "filtered" / f"{region}.pois.parquet"


def restrictions_parquet(region: str) -> Path:
    """The region's turn restrictions."""
    return settings().data_dir / "filtered" / f"{region}.restrictions.parquet"


def build_dir(region: str, build_id: str) -> Path:
    """One build of the region."""
    return settings().data_dir / "graphs" / region / build_id


def stage_file(build: Path, name: str) -> Path:
    """An intermediate stage file of a build, e.g. `build.pkl`."""
    return build / "stages" / name


# The file an arrays engine stage directory is complete with, written last (`arrays/stage.py`).
STAGE_MANIFEST = "manifest.json"
# The stage each engine's `build`, `cleanup` and `enrich` write, and the file that stands for it
# in the next step's sidecar: networkx's pickles, the arrays engine's directories.
BUILD_STAGES: dict[PipelineEngine, tuple[str, str]] = {
    "networkx": ("build.pkl", "build.pkl"),
    "arrays": ("build", f"build/{STAGE_MANIFEST}"),
}
CLEANUP_STAGES: dict[PipelineEngine, tuple[str, str]] = {
    "networkx": ("cleanup.pkl", "cleanup.pkl"),
    "arrays": ("cleanup", f"cleanup/{STAGE_MANIFEST}"),
}
ENRICH_STAGES: dict[PipelineEngine, tuple[str, str]] = {
    "networkx": ("enrich.pkl", "enrich.pkl"),
    "arrays": ("enrich", f"enrich/{STAGE_MANIFEST}"),
}


def enriched_stages(engine: PipelineEngine) -> list[str]:
    """The stages the enriched graph is read from, as sidecars name them: the networkx engine's
    `enrich.pkl`, which holds the whole graph; the arrays engine's enrich stage and the cleanup
    stage it lies on."""
    if engine == "arrays":
        return [ENRICH_STAGES[engine][1], CLEANUP_STAGES[engine][1]]
    return [ENRICH_STAGES[engine][1]]


def cleanup_engine(build: Path) -> PipelineEngine | None:
    """The engine the build's cleanup ran, whose stage the build holds (cleanup removes the
    other engine's), or None before cleanup has run. A build cleaned by the arrays engine before
    it wrote `stages/cleanup/` holds a handed-over `cleanup.pkl` and reads as the networkx
    engine's; the default cleanup then reruns into `stages/cleanup/` (`pipeline.md`)."""
    for engine, (_, recorded) in CLEANUP_STAGES.items():
        if stage_file(build, recorded).exists():
            return engine
    return None


def build_stage(build: Path) -> Path:
    """The build stage the build's cleanup read, as its sidecar records (the default engine's
    before cleanup has run)."""
    engine = cleanup_engine(build)
    side = sidecar(stage_file(build, CLEANUP_STAGES[engine][0])) if engine else None
    if side is not None and side.exists():
        read = {i["file"] for i in json.loads(side.read_text())["inputs"]}
        for stage, recorded in BUILD_STAGES.values():
            if recorded in read:
                return stage_file(build, stage)
    return stage_file(build, BUILD_STAGES[DEFAULT_PIPELINE_ENGINE][0])


def qa_file(build: Path, name: str) -> Path:
    """A file of a build's QA report, e.g. `qa.json`."""
    return build / "qa" / name


def latest_file(region: str) -> Path:
    """The file naming the region's current build."""
    return settings().data_dir / "graphs" / region / "latest"


def build_id_for(roads_md5: str, region: Region) -> str:
    """The id of the build of `region` from filtered roads with md5 `roads_md5`."""
    key = json.dumps([roads_md5, region.name, region.bbox, region.crs_epsg, PIPELINE_VERSION])
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def sidecar(output: Path) -> Path:
    """The file recording what `output` was made from."""
    return output.with_name(f"{output.name}.inputs.json")


# A build's stages in order: each step, a stage it writes (either engine's), and the stage files
# its sidecar may record as its inputs (cleanup reads one engine's build stage or the other's,
# and each later step its own engine's stages; tiers and save read the enriched graph from
# `enriched_stages`).
_ENRICHED = tuple(dict.fromkeys(name for e in ENRICH_STAGES for name in enriched_stages(e)))
STAGE_CHAIN: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    *(
        ("cleanup", stage, tuple(recorded for _, recorded in BUILD_STAGES.values()))
        for stage, _ in CLEANUP_STAGES.values()
    ),
    *(("enrich", ENRICH_STAGES[e][0], (CLEANUP_STAGES[e][1],)) for e in ENRICH_STAGES),
    ("tiers", "tiers.json", _ENRICHED),
    ("save", "save.json", (*_ENRICHED, "tiers.json")),
)


def stale_stages(build: Path, up_to: str) -> list[str]:
    """The stages before step `up_to`, in order, that were made from inputs since changed.

    A stage is stale when its sidecar exists and a stage input it records is gone or has another
    md5 now, or it records none; a stage not yet made is not stale (a step's own missing input is
    reported apart). A step outside the chain checks every stage.
    """
    stale = []
    for step, output, inputs in STAGE_CHAIN:
        if step == up_to:
            break
        side = sidecar(stage_file(build, output))
        if not side.exists():
            continue
        recorded = {i["file"]: i["md5"] for i in json.loads(side.read_text())["inputs"]}
        names = [name for name in inputs if name in recorded]
        if not names:
            stale.append(step)
            continue
        for name in names:
            path = stage_file(build, name)
            if not path.exists() or recorded[name] != file_md5(path):
                stale.append(step)
                break
    return stale


def sidecar_matches(output: Path, record: dict[str, Any]) -> bool:
    """Whether `output` exists and its sidecar holds `record`."""
    side = sidecar(output)
    if not (output.exists() and side.exists()):
        return False
    return bool(json.loads(side.read_text()) == json.loads(json.dumps(record)))


def write_sidecar(output: Path, record: dict[str, Any]) -> None:
    """Record what `output` was made from."""
    sidecar(output).write_text(json.dumps(record, indent=2) + "\n")
