"""Where the pipeline's files live, all under `settings().data_dir` (`docs/reference/pipeline.md`).

Raw extracts are `raw/<state>-latest.osm.pbf`, each with the `.md5` file Geofabrik publishes
beside it; a region's filtered roads and POIs are `filtered/<region>.roads.osm.pbf` and
`filtered/<region>.pois.parquet`. Paths are computed on every call, so a test that points
`WORST_ROUTE_DATA` elsewhere and clears the settings cache sees its own tree.
"""

import hashlib
from pathlib import Path

from worst_route.config import settings


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
