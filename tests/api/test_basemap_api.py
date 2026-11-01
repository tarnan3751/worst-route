"""`GET /config` and the basemap's files under `/basemap/`: what the frontend is told to draw,
byte ranges on a small file standing for a `.pmtiles` (any bytes do: the server never reads
them), `/ready`'s basemap check, and one file named for every build served."""

from pathlib import Path

import pytest

from worst_route.config import API_MAX_JOBS, MAX_PORTFOLIO

from .hosting import hosted

pytestmark = pytest.mark.anyio

SIZE = 16384
TILES = bytes(range(256)) * (SIZE // 256)


def write_tiles(data: Path) -> Path:
    path = data / "basemap" / "portland.pmtiles"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TILES)
    return path


def write_assets(data: Path) -> None:
    fonts = data / "basemap" / "fonts" / "Noto Sans Regular"
    fonts.mkdir(parents=True)
    (fonts / "0-255.pbf").write_bytes(b"\x0a\x00")
    sprites = data / "basemap" / "sprites"
    sprites.mkdir(parents=True)
    (sprites / "grayscale.json").write_text("{}")


async def test_config_names_the_basemap_and_the_limits(data: Path) -> None:
    async with hosted(data) as (http, _):
        assert (await http.get("/config")).json() == {
            "public": False,
            "basemap": {
                "kind": "raster",
                "tiles": None,
                "glyphs": None,
                "sprite": None,
                "style": None,
            },
            "limits": {
                "budget_max_s": 120.0,
                "leash_max_km": 150.0,
                "portfolio_max": min(MAX_PORTFOLIO, API_MAX_JOBS),
            },
        }
    async with hosted(data, public=True, basemap="pmtiles") as (http, _):
        config = (await http.get("/config")).json()
        assert config["public"] is True and config["limits"]["budget_max_s"] == 60.0
        assert config["basemap"] == {  # no fonts or icons: drawn without labels
            "kind": "pmtiles",
            "tiles": "/basemap/portland.pmtiles",
            "glyphs": None,
            "sprite": None,
            "style": None,
        }
        write_assets(data)
        basemap = (await http.get("/config")).json()["basemap"]
        assert basemap["glyphs"] == "/basemap/fonts/{fontstack}/{range}.pbf"
        assert basemap["sprite"] == "/basemap/sprites/grayscale"


async def test_config_names_the_most_seeds_a_job_runs(data: Path) -> None:
    async with hosted(data, jobs={"max_jobs": 8}) as (http, _):
        assert (await http.get("/config")).json()["limits"]["portfolio_max"] == 4
    async with hosted(data, public=True, jobs={"max_jobs": 8}) as (http, _):
        assert (await http.get("/config")).json()["limits"]["portfolio_max"] == 2
    async with hosted(data, jobs={"max_jobs": 3}) as (http, _):  # never more than its workers
        assert (await http.get("/config")).json()["limits"]["portfolio_max"] == 3


async def test_openfreemap_names_its_style(data: Path) -> None:
    # A third party's tiles: no file of the server's, nothing for /ready to check.
    from ..presentation.support import write_fixture_places

    write_fixture_places(data)
    async with hosted(data, basemap="openfreemap") as (http, _):
        assert (await http.get("/config")).json()["basemap"] == {
            "kind": "openfreemap",
            "tiles": None,
            "glyphs": None,
            "sprite": None,
            "style": "https://tiles.openfreemap.org/styles/positron",
        }
        answer = await http.get("/ready")
        assert answer.status_code == 200
        [basemap] = [c for c in answer.json()["checks"] if c["name"] == "basemap"]
        assert basemap["detail"] == "openfreemap: OpenFreeMap's tiles"


async def test_the_tiles_are_served_with_byte_ranges(data: Path) -> None:
    write_tiles(data)
    async with hosted(data, basemap="pmtiles") as (http, _):
        url = "/basemap/portland.pmtiles"
        whole = await http.get(url)
        assert whole.status_code == 200 and whole.content == TILES
        assert whole.headers["content-type"] == "application/vnd.pmtiles"
        assert whole.headers["accept-ranges"] == "bytes"
        assert whole.headers["cache-control"] == "public, max-age=86400"
        etag = whole.headers["etag"]
        head = await http.get(url, headers={"Range": "bytes=0-126"})  # the PMTiles header
        assert head.status_code == 206 and head.content == TILES[:127]
        assert head.headers["content-range"] == f"bytes 0-126/{SIZE}"
        assert head.headers["etag"] == etag  # the same file: pmtiles checks it stays so
        tail = await http.get(url, headers={"Range": "bytes=-100"})
        assert tail.status_code == 206 and tail.content == TILES[-100:]
        middle = await http.get(url, headers={"Range": f"bytes=16000-{SIZE + 50}"})
        assert middle.status_code == 206 and middle.content == TILES[16000:]
        past = await http.get(url, headers={"Range": "bytes=20000-20100"})
        assert past.status_code == 416  # pmtiles reads the length from this answer
        assert past.headers["content-range"] == f"bytes */{SIZE}"
        same = await http.get(url, headers={"Range": "bytes=10-19", "If-Range": etag})
        assert same.status_code == 206 and same.content == TILES[10:20]
        stale = await http.get(url, headers={"Range": "bytes=10-19", "If-Range": '"old"'})
        assert stale.status_code == 200 and stale.content == TILES  # the file changed: all of it
        only = await http.head(url)
        assert only.status_code == 200 and only.content == b""
        assert only.headers["content-length"] == str(SIZE)


async def test_fonts_and_missing_files(data: Path) -> None:
    async with hosted(data) as (http, _):
        missing = await http.get("/basemap/portland.pmtiles")  # no basemap directory at all
        assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"
        write_tiles(data)
        write_assets(data)
        font = await http.get("/basemap/fonts/Noto%20Sans%20Regular/0-255.pbf")
        assert font.status_code == 200 and font.content == b"\x0a\x00"
        assert font.headers["content-type"] == "application/x-protobuf"
        assert (await http.get("/basemap/fonts/Noto%20Sans%20Regular/256-511.pbf")).status_code == (
            404
        )
        (data / "secret.txt").write_text("not served")
        assert (await http.get("/basemap/%2e%2e/secret.txt")).status_code == 404


async def test_a_linked_file_and_a_small_one(data: Path, tmp_path: Path) -> None:
    # The operator may keep the tiles elsewhere and link them in; /ready and /basemap/ agree.
    kept = tmp_path / "elsewhere" / "us.pmtiles"
    kept.parent.mkdir()
    kept.write_bytes(TILES[:1000])
    (data / "basemap").mkdir()
    (data / "basemap" / "portland.pmtiles").symlink_to(kept)
    async with hosted(data, basemap="pmtiles") as (http, _):
        [basemap] = [
            c for c in (await http.get("/ready")).json()["checks"] if c["name"] == "basemap"
        ]
        assert basemap["ok"] is True
        # The reader's first read asks for 16 kB: a smaller file is all of it, cut at its end.
        first = await http.get("/basemap/portland.pmtiles", headers={"Range": "bytes=0-16383"})
        assert first.status_code == 206 and first.content == TILES[:1000]
        assert first.headers["content-range"] == "bytes 0-999/1000"


async def test_ready_needs_the_tiles_of_a_pmtiles_basemap(data: Path) -> None:
    from ..presentation.support import write_fixture_places

    write_fixture_places(data)
    async with hosted(data, basemap="pmtiles") as (http, _):
        answer = await http.get("/ready")
        assert answer.status_code == 503
        [basemap] = [c for c in answer.json()["checks"] if c["name"] == "basemap"]
        assert basemap["detail"] == (
            "no basemap file portland.pmtiles: run `tools/fetch_basemap.sh portland`"
        )
        assert basemap["ok"] is False and str(data) not in answer.text
        write_tiles(data)
        answer = await http.get("/ready")
        assert answer.status_code == 200
        [basemap] = [c for c in answer.json()["checks"] if c["name"] == "basemap"]
        assert basemap["detail"] == "portland.pmtiles, 0.0 MB, without labels (--assets)"
        write_assets(data)
        [basemap] = [
            c for c in (await http.get("/ready")).json()["checks"] if c["name"] == "basemap"
        ]
        assert basemap["detail"].endswith("with labels")


async def test_a_basemap_directory_of_its_own(data: Path, tmp_path: Path) -> None:
    # The desktop app's data is read only: its basemap is downloaded into a directory apart.
    from ..presentation.support import write_fixture_places

    write_fixture_places(data)
    own = tmp_path / "user" / "basemap"
    write_tiles(data)  # never read: the directory named is the basemap's
    async with hosted(data, basemap="pmtiles", basemap_dir=own) as (http, _):
        assert (await http.get("/ready")).status_code == 503
        assert (await http.get("/basemap/portland.pmtiles")).status_code == 404
        write_tiles(own.parent)
        write_assets(own.parent)
        basemap = (await http.get("/config")).json()["basemap"]
        assert basemap["tiles"] == "/basemap/portland.pmtiles" and basemap["sprite"] is not None
        assert (await http.get("/ready")).status_code == 200
        assert (await http.get("/basemap/portland.pmtiles")).content == TILES


async def test_one_named_basemap_serves_every_build(lands: Path) -> None:
    # The file is named by basemap_name, by default the first region served.
    both = (("westland", "latest"), ("lands", "latest"))
    async with hosted(lands, builds=both, basemap="pmtiles") as (http, _):
        basemap = (await http.get("/config")).json()["basemap"]
        assert basemap["tiles"] == "/basemap/westland.pmtiles"
    async with hosted(lands, builds=both, basemap="pmtiles", basemap_name="us") as (http, _):
        assert (await http.get("/config")).json()["basemap"]["tiles"] == "/basemap/us.pmtiles"
        answer = await http.get("/ready")
        [basemap] = [c for c in answer.json()["checks"] if c["name"] == "basemap"]
        assert basemap["detail"] == "no basemap file us.pmtiles: run `tools/fetch_basemap.sh us`"
        path = lands / "basemap" / "us.pmtiles"
        path.parent.mkdir(parents=True)
        path.write_bytes(TILES)
        [basemap] = [
            c for c in (await http.get("/ready")).json()["checks"] if c["name"] == "basemap"
        ]
        assert basemap == {
            "name": "basemap",
            "ok": True,
            "detail": "us.pmtiles, 0.0 MB, without labels (--assets)",
        }
        assert (await http.get("/basemap/us.pmtiles")).content == TILES
