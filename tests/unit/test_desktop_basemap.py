"""The desktop app's basemap file (`worst_route.desktop_basemap`), over an `httpx.MockTransport`
that serves byte ranges as GitHub's release storage does: fetched whole, resumed, refused when
its checksum fails, kept for later when the network fails; and the labels' assets copied once."""

import hashlib
from pathlib import Path

import httpx
import pytest

from worst_route.desktop_basemap import BasemapAsset, DownloadError, fetch, install_assets

BYTES = bytes(range(256)) * 40  # 10,240 bytes
URL = "https://releases.example/us.pmtiles"
ASSET = BasemapAsset("us", URL, len(BYTES), hashlib.sha256(BYTES).hexdigest())
AGENT = "worst-route-desktop/0.1.0 (https://github.com/tarnan3751/worst-route)"


def serving(body: bytes, seen: list[httpx.Request], ranges: bool = True) -> httpx.Client:
    """A client of a server holding `body`, answering a `Range` with 206 (or 200 without
    `ranges`), recording each request in `seen`."""

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        asked = request.headers.get("range")
        if asked and ranges:
            start = int(asked.removeprefix("bytes=").removesuffix("-"))
            headers = {"content-range": f"bytes {start}-{len(body) - 1}/{len(body)}"}
            return httpx.Response(206, content=body[start:], headers=headers)
        return httpx.Response(200, content=body)

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_fetched_whole_checked_and_renamed(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    progress: list[int] = []
    path = fetch(
        ASSET,
        tmp_path / "basemap",
        AGENT,
        lambda have, size: progress.append(have),
        serving(BYTES, seen),
    )
    assert path == tmp_path / "basemap" / "us.pmtiles" and path.read_bytes() == BYTES
    assert not (tmp_path / "basemap" / "us.pmtiles.part").exists()
    assert "range" not in seen[0].headers and progress[-1] == len(BYTES)
    assert fetch(ASSET, tmp_path / "basemap", AGENT, client=serving(BYTES, seen)) == path
    assert len(seen) == 1  # there already: nothing asked


def test_a_partial_file_is_resumed_from_where_it_stopped(tmp_path: Path) -> None:
    (tmp_path / "us.pmtiles.part").write_bytes(BYTES[:4000])
    seen: list[httpx.Request] = []
    path = fetch(ASSET, tmp_path, AGENT, client=serving(BYTES, seen))
    assert seen[0].headers["range"] == "bytes=4000-" and path.read_bytes() == BYTES
    # A server that ignores the range sends it whole, and it is written afresh.
    path.unlink()
    (tmp_path / "us.pmtiles.part").write_bytes(BYTES[:4000])
    assert fetch(ASSET, tmp_path, AGENT, client=serving(BYTES, [], ranges=False)) == path
    assert path.read_bytes() == BYTES


def test_a_file_that_is_not_the_one_named_is_refused_and_dropped(tmp_path: Path) -> None:
    other = bytes(reversed(BYTES))
    with pytest.raises(DownloadError, match="not the file named"):
        fetch(ASSET, tmp_path, AGENT, client=serving(other, []))
    assert list(tmp_path.iterdir()) == []  # the next start fetches it afresh
    (tmp_path / "us.pmtiles.part").write_bytes(BYTES + b"more")  # longer than the file
    assert fetch(ASSET, tmp_path, AGENT, client=serving(BYTES, [])).read_bytes() == BYTES


def test_a_failed_request_keeps_the_part_for_the_next_start(tmp_path: Path) -> None:
    (tmp_path / "us.pmtiles.part").write_bytes(BYTES[:100])

    def gone(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(gone))
    with pytest.raises(DownloadError, match="answered 404"):
        fetch(ASSET, tmp_path, AGENT, client=client)
    assert (tmp_path / "us.pmtiles.part").read_bytes() == BYTES[:100]
    with pytest.raises(DownloadError, match="no address"):
        fetch(BasemapAsset("us", None, 1, "00"), tmp_path, AGENT)


def test_the_labels_fonts_and_icons_are_copied_once(tmp_path: Path) -> None:
    source = tmp_path / "data" / "basemap"
    (source / "fonts" / "Noto Sans Regular").mkdir(parents=True)
    (source / "fonts" / "Noto Sans Regular" / "0-255.pbf").write_bytes(b"\x0a")
    (source / "sprites").mkdir()
    (source / "sprites" / "grayscale.json").write_text("{}")
    target = tmp_path / "user" / "basemap"
    install_assets(source, target)
    assert (target / "fonts" / "Noto Sans Regular" / "0-255.pbf").read_bytes() == b"\x0a"
    assert (target / "sprites" / "grayscale.json").read_text() == "{}"
    (source / "sprites" / "grayscale.json").write_text('{"newer": true}')
    install_assets(source, target)
    assert (target / "sprites" / "grayscale.json").read_text() == "{}"  # once
    install_assets(tmp_path / "nothing", tmp_path / "elsewhere")  # no assets: nothing made
    assert not (tmp_path / "elsewhere").exists()
