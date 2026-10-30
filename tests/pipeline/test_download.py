import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from worst_route.config import settings
from worst_route.pipeline import download

EXTRACT = b"pretend this is oregon-latest.osm.pbf"
URL = "https://download.geofabrik.de/north-america/us/oregon-latest.osm.pbf"


class FakeFetch:
    """Serves fixed bytes per URL and records the URLs asked for."""

    def __init__(self, extract: bytes, published: bytes) -> None:
        md5 = hashlib.md5(published).hexdigest()
        self.content = {URL: extract, f"{URL}.md5": f"{md5}  oregon-latest.osm.pbf\n".encode()}
        self.urls: list[str] = []

    def __call__(self, url: str, dest: Path) -> None:
        self.urls.append(url)
        dest.write_bytes(self.content[url])


def test_fresh_download(data_dir: Path) -> None:
    fake = FakeFetch(EXTRACT, EXTRACT)
    path, skipped = download.download_state("oregon", fetch=fake)
    assert not skipped
    assert path == data_dir / "raw" / "oregon-latest.osm.pbf"
    assert path.read_bytes() == EXTRACT
    assert download.read_md5(path.with_name("oregon-latest.osm.pbf.md5")) == (
        hashlib.md5(EXTRACT).hexdigest()
    )
    assert fake.urls == [f"{URL}.md5", URL]
    assert sorted(p.name for p in path.parent.iterdir()) == [
        "oregon-latest.osm.pbf",
        "oregon-latest.osm.pbf.md5",
    ]
    assert download.is_current("oregon")


def test_current_extract_is_not_fetched_again(data_dir: Path) -> None:
    download.download_state("oregon", fetch=FakeFetch(EXTRACT, EXTRACT))
    fake = FakeFetch(b"a newer extract", b"a newer extract")
    path, skipped = download.download_state("oregon", fetch=fake)
    assert skipped
    assert fake.urls == []
    assert path.read_bytes() == EXTRACT


def test_force_fetches_again(data_dir: Path) -> None:
    download.download_state("oregon", fetch=FakeFetch(EXTRACT, EXTRACT))
    fake = FakeFetch(b"a newer extract", b"a newer extract")
    path, skipped = download.download_state("oregon", force=True, fetch=fake)
    assert not skipped
    assert fake.urls == [f"{URL}.md5", URL]
    assert path.read_bytes() == b"a newer extract"
    assert download.is_current("oregon")


def test_stale_extract_is_fetched_again(data_dir: Path) -> None:
    path, _ = download.download_state("oregon", fetch=FakeFetch(EXTRACT, EXTRACT))
    path.write_bytes(b"truncated")
    assert not download.is_current("oregon")
    fake = FakeFetch(EXTRACT, EXTRACT)
    assert not download.download_state("oregon", fetch=fake).skipped
    assert fake.urls == [f"{URL}.md5", URL]
    assert path.read_bytes() == EXTRACT


def test_md5_mismatch_raises_and_keeps_nothing(data_dir: Path) -> None:
    with pytest.raises(ValueError, match="does not match"):
        download.download_state("oregon", fetch=FakeFetch(EXTRACT, b"something else"))
    assert list((data_dir / "raw").iterdir()) == []


def test_md5_mismatch_leaves_the_previous_extract(data_dir: Path) -> None:
    path, _ = download.download_state("oregon", fetch=FakeFetch(EXTRACT, EXTRACT))
    with pytest.raises(ValueError, match="does not match"):
        download.download_state("oregon", force=True, fetch=FakeFetch(b"new", b"other"))
    assert path.read_bytes() == EXTRACT
    assert download.is_current("oregon")
    assert sorted(p.name for p in path.parent.iterdir()) == [
        "oregon-latest.osm.pbf",
        "oregon-latest.osm.pbf.md5",
    ]


def test_fetch_streams_with_the_user_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def iter_bytes(self, chunk_size: int) -> Iterator[bytes]:
            yield from (b"ab", b"cd")

    @contextmanager
    def stream(method: str, url: str, **kwargs: Any) -> Iterator[Response]:
        seen.update(method=method, url=url, **kwargs)
        yield Response()

    monkeypatch.setattr(httpx, "stream", stream)
    download.fetch(URL, tmp_path / "out")
    assert (tmp_path / "out").read_bytes() == b"abcd"
    assert (seen["method"], seen["url"]) == ("GET", URL)
    assert seen["headers"] == {"User-Agent": settings().user_agent}
    assert seen["follow_redirects"] is True


def test_read_md5(tmp_path: Path) -> None:
    md5_file = tmp_path / "x.md5"
    md5_file.write_text("1DE27CC62802F3DB82645EC52A41026F  oregon-latest.osm.pbf\n")
    assert download.read_md5(md5_file) == "1de27cc62802f3db82645ec52a41026f"
