"""The desktop app's basemap file (`docs/reference/desktop.md`, **The basemap**): Protomaps'
tiles of the contiguous US, a release asset the app fetches once, in the background, into the
user's directory, after which the API serves it as the `pmtiles` basemap.

`fetch(asset, directory, agent, progress)` downloads `asset.url` (redirects followed, the app's
User-Agent sent) into `<name>.pmtiles.part` beside its final name, asking with `Range` for what a
previous start did not get (a server that answers 200 instead sends it whole again), then checks
the whole file's size and sha256 against the manifest's: a mismatch deletes the part (its next
start fetches it afresh) and raises `DownloadError`, a match renames it to `<name>.pmtiles` at once,
so the file the API serves is always whole and checked. An error on the way (the network, a
status other than 200 or 206) keeps the part for the next start to resume. `progress(have, size)`
is called as bytes arrive. Two apps fetching at once would interleave their bytes; the checksum
refuses the result and the next start fetches it again.

`install_assets(source, target)` copies the labels' fonts and icons (`tools/fetch_basemap.sh
--assets`, inside the app's data) into the user's basemap directory, once, so the API serves the
whole basemap from one directory.
"""

import hashlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

CHUNK = 1 << 20
TIMEOUT = httpx.Timeout(60.0, connect=30.0)


@dataclass(frozen=True)
class BasemapAsset:
    """The basemap's file: its name (`<name>.pmtiles`), where it is fetched from (None: nowhere
    yet), and its size and sha256 (hex)."""

    name: str
    url: str | None
    size: int
    sha256: str


class DownloadError(Exception):
    """The basemap's file did not arrive whole."""


def tiles_path(directory: Path, asset: BasemapAsset) -> Path:
    """Where the checked file lies."""
    return directory / f"{asset.name}.pmtiles"


def fetch(
    asset: BasemapAsset,
    directory: Path,
    agent: str,
    progress: Callable[[int, int], None] = lambda have, size: None,
    client: httpx.Client | None = None,
) -> Path:
    """`asset` in `directory`, fetched or resumed and checked; its path."""
    final = tiles_path(directory, asset)
    if final.is_file():
        return final
    if not asset.url:
        raise DownloadError(f"{final.name} has no address to fetch it from")
    directory.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > asset.size:
        part.unlink()
        have = 0
    if have < asset.size:
        own = client is None
        http = client or httpx.Client(
            headers={"User-Agent": agent}, timeout=TIMEOUT, follow_redirects=True
        )
        try:
            headers = {"Range": f"bytes={have}-"} if have else {}
            with http.stream("GET", asset.url, headers=headers) as response:
                if response.status_code == 200:
                    have = 0  # the whole file again
                elif response.status_code != 206:
                    raise DownloadError(f"{asset.url} answered {response.status_code}")
                elif not response.headers.get("content-range", "").startswith(f"bytes {have}-"):
                    raise DownloadError(f"{asset.url} answered another range than asked")
                with part.open("ab" if have else "wb") as out:
                    for chunk in response.iter_bytes(CHUNK):
                        out.write(chunk)
                        have += len(chunk)
                        progress(have, asset.size)
        finally:
            if own:
                http.close()
    if have != asset.size or _sha256(part) != asset.sha256.lower():
        part.unlink()
        raise DownloadError(f"{final.name} arrived with {have:,} bytes, not the file named")
    os.replace(part, final)
    return final


def install_assets(source: Path, target: Path) -> None:
    """The fonts and icons under `source` (`fonts/`, `sprites/`) copied into `target`, each
    directory once: an older copy stays."""
    for name in ("fonts", "sprites"):
        if (source / name).is_dir() and not (target / name).exists():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source / name, target / f".{name}.part", dirs_exist_ok=True)
            os.replace(target / f".{name}.part", target / name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()
