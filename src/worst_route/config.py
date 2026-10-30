"""Settings and named regions.

`Settings` holds what varies per machine, read from the environment: the data directory
(`WORST_ROUTE_DATA`, default `./data`; `CLAUDE.md`, **Commands**) and the User-Agent sent to
outside services. `settings()` returns one cached instance; tests that change the environment
call `settings.cache_clear()`.

`REGIONS` names the areas the pipeline builds (`docs/PLAN.md` §2): each is a Geofabrik state
extract, optionally clipped to a bbox, with the projected CRS its metric steps use. Speeds,
tier definitions, class allowlists and defaults join this module as their steps are built.
"""

from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Per-machine settings, read from `WORST_ROUTE_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="WORST_ROUTE_", populate_by_name=True)

    data_dir: Path = Field(default=Path("data"), validation_alias="WORST_ROUTE_DATA")
    user_agent: str = "worst-route/0.1.0 (https://github.com/tarnan3751/worst-route)"


class Region(BaseModel):
    """A named build area: a Geofabrik state extract, optionally clipped to a bbox."""

    name: str
    state: str  # Geofabrik slug, e.g. "oregon"
    bbox: tuple[float, float, float, float] | None = None  # (min_lon, min_lat, max_lon, max_lat)
    crs_epsg: int  # projected CRS for the metric steps


REGIONS: dict[str, Region] = {
    "portland": Region(
        name="portland",
        state="oregon",
        bbox=(-123.05, 45.25, -122.35, 45.72),
        crs_epsg=32610,
    ),
    "oregon": Region(name="oregon", state="oregon", crs_epsg=32610),
}


@cache
def settings() -> Settings:
    """The process-wide settings, read from the environment once."""
    return Settings()
