"""`pipeline download`: fetch a Geofabrik state extract and verify it (`docs/PLAN.md` §2.1).

`download_state` fetches `<state>-latest.osm.pbf` and its `.md5` from Geofabrik's US directory
into `raw/` (`layout.py`), each to a `.part` file that is renamed into place only once the
extract matches the published md5; a mismatch raises and leaves the previous files as they were.
An extract that matches its local `.md5` file is not fetched again unless forced, and the check
reads only local files; the result says which happened. `fetch` is the one function that touches
the network; tests pass a fake.
"""

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import httpx

from worst_route.config import settings
from worst_route.pipeline import layout

GEOFABRIK_US = "https://download.geofabrik.de/north-america/us"

type Fetch = Callable[[str, Path], None]


class Download(NamedTuple):
    """The state's extract, and whether it was already current (so nothing was fetched)."""

    path: Path
    skipped: bool


def fetch(url: str, dest: Path) -> None:
    """Stream `url` into `dest`, sending the project's User-Agent."""
    headers = {"User-Agent": settings().user_agent}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        with dest.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)


def read_md5(md5_file: Path) -> str:
    """The checksum in a Geofabrik `.md5` file (`<hex>  <filename>`)."""
    return md5_file.read_text().split()[0].lower()


def is_current(state: str) -> bool:
    """Whether the state's extract is on disk and matches its `.md5` file."""
    pbf, md5 = layout.raw_pbf(state), layout.raw_md5(state)
    return pbf.exists() and md5.exists() and layout.file_md5(pbf) == read_md5(md5)


def download_state(state: str, *, force: bool = False, fetch: Fetch = fetch) -> Download:
    """Fetch the state's extract and its `.md5` unless the extract on disk is current."""
    pbf, md5 = layout.raw_pbf(state), layout.raw_md5(state)
    if not force and is_current(state):
        return Download(pbf, skipped=True)
    pbf.parent.mkdir(parents=True, exist_ok=True)
    pbf_part, md5_part = pbf.with_name(f"{pbf.name}.part"), md5.with_name(f"{md5.name}.part")
    try:
        fetch(f"{GEOFABRIK_US}/{md5.name}", md5_part)
        expected = read_md5(md5_part)
        fetch(f"{GEOFABRIK_US}/{pbf.name}", pbf_part)
        actual = layout.file_md5(pbf_part)
        if actual != expected:
            raise ValueError(f"{pbf.name}: md5 {actual} does not match the published {expected}")
        pbf_part.replace(pbf)
        md5_part.replace(md5)
    finally:
        pbf_part.unlink(missing_ok=True)
        md5_part.unlink(missing_ok=True)
    return Download(pbf, skipped=False)
