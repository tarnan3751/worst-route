"""Where the pipeline's files live, all under `settings().data_dir` (`docs/reference/pipeline.md`).

Raw extracts are `raw/<state>-latest.osm.pbf`, each with the `.md5` file Geofabrik publishes
beside it; a region's filtered roads and POIs are `filtered/<region>.roads.osm.pbf` and
`filtered/<region>.pois.parquet`. A build is `graphs/<region>/<build_id>/`, its intermediate
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
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from worst_route.config import Region, settings
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


def build_dir(region: str, build_id: str) -> Path:
    """One build of the region."""
    return settings().data_dir / "graphs" / region / build_id


def stage_file(build: Path, name: str) -> Path:
    """An intermediate stage file of a build, e.g. `build.pkl`."""
    return build / "stages" / name


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


# A build's stages in order: each step, the stage file it writes, and the stage files its
# sidecar records as its inputs.
STAGE_CHAIN: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("cleanup", "cleanup.pkl", ("build.pkl",)),
    ("enrich", "enrich.pkl", ("cleanup.pkl",)),
    ("tiers", "tiers.json", ("enrich.pkl",)),
    ("save", "save.json", ("enrich.pkl", "tiers.json")),
)


def stale_stages(build: Path, up_to: str) -> list[str]:
    """The stages before step `up_to`, in order, that were made from inputs since changed.

    A stage is stale when its sidecar exists and an input it records is gone or has another md5
    now; a stage not yet made is not stale (a step's own missing input is reported apart). A step
    outside the chain checks every stage.
    """
    stale = []
    for step, output, inputs in STAGE_CHAIN:
        if step == up_to:
            break
        side = sidecar(stage_file(build, output))
        if not side.exists():
            continue
        recorded = {i["file"]: i["md5"] for i in json.loads(side.read_text())["inputs"]}
        for name in inputs:
            path = stage_file(build, name)
            if not path.exists() or recorded.get(name) != file_md5(path):
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
