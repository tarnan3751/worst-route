"""The cache: misses, answers (empty ones too), the TTL on a fake clock, what the key covers."""

import sqlite3
from contextlib import closing
from pathlib import Path

from worst_route.geocode.cache import GeocodeCache, key

from .conftest import PORTLAND, POWELLS, FakeTime


def test_a_stored_answer_comes_back_from_any_instance(tmp_path: Path) -> None:
    path = tmp_path / "geocode.sqlite"
    cache = GeocodeCache(path, clock=FakeTime(1000.0).clock)
    assert cache.get("portland", PORTLAND, "powell's") is None
    cache.put("portland", PORTLAND, "powell's", [POWELLS])
    assert cache.get("portland", PORTLAND, "powell's") == [POWELLS]
    assert GeocodeCache(path, clock=FakeTime(1000.0).clock).get(
        "portland", PORTLAND, "powell's"
    ) == [POWELLS]


def test_an_answer_of_nothing_is_an_answer(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path / "geocode.sqlite")
    cache.put("portland", PORTLAND, "atlantis", [])
    assert cache.get("portland", PORTLAND, "atlantis") == []


def test_an_answer_expires_after_its_ttl_and_is_replaced(tmp_path: Path) -> None:
    time = FakeTime(0.0)
    path = tmp_path / "geocode.sqlite"
    cache = GeocodeCache(path, ttl_s=100.0, clock=time.clock)
    cache.put("portland", PORTLAND, "powell's", [POWELLS])
    time.now = 99.9
    assert cache.get("portland", PORTLAND, "powell's") == [POWELLS]
    time.now = 100.0
    assert cache.get("portland", PORTLAND, "powell's") is None
    cache.put("portland", PORTLAND, "powell's", [])
    time.now = 150.0
    assert cache.get("portland", PORTLAND, "powell's") == []
    with closing(sqlite3.connect(path)) as con:
        assert con.execute("SELECT COUNT(*), MAX(created) FROM queries").fetchone() == (1, 100.0)


def test_the_key_covers_the_region_its_bbox_to_4_decimals_and_the_query(tmp_path: Path) -> None:
    k = key("portland", PORTLAND, "powell's")
    assert len(k) == 64
    assert key("oregon", PORTLAND, "powell's") != k
    assert key("portland", PORTLAND, "powells") != k
    moved = (PORTLAND[0] + 0.001, *PORTLAND[1:])
    assert key("portland", moved, "powell's") != k
    assert key("portland", (PORTLAND[0] + 1e-6, *PORTLAND[1:]), "powell's") == k
    cache = GeocodeCache(tmp_path / "geocode.sqlite")
    cache.put("portland", PORTLAND, "powell's", [POWELLS])
    assert cache.get("portland", moved, "powell's") is None
    assert cache.get("oregon", PORTLAND, "powell's") is None
