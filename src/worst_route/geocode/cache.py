"""The geocoder's cache (`docs/reference/api.md`, **Geocoding**): answers in sqlite.

    queries(key TEXT PRIMARY KEY, created REAL, body TEXT)

`key(region, bbox, query)` is the sha256 of the region, the bbox's four numbers rounded to 4
decimals and the normalised query, one per line, so a query is cached per region and per area:
a rebuilt region whose bbox moved asks again. `body` is the candidates as JSON (an empty list
too: an answer of nothing is an answer) and `created` when it was stored, in seconds from
`clock` (the epoch). `get` answers None for a key not stored, or stored `ttl_s` ago or longer;
`put` stores an answer, replacing an expired one. Only answers are stored, never errors.
`GeocodeCache(path)` creates the file and its table when missing and opens a connection per
call, in WAL mode like the route store, so it may be used from any thread.
"""

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from worst_route.config import GEOCODE_CACHE_TTL_S
from worst_route.geocode.base import Bbox, Candidate

_SCHEMA = "CREATE TABLE IF NOT EXISTS queries (key TEXT PRIMARY KEY, created REAL, body TEXT)"


def key(region: str, bbox: Bbox, query: str) -> str:
    """The cache key of a normalised `query` in `region`'s `bbox`."""
    area = ",".join(f"{v:.4f}" for v in bbox)
    return hashlib.sha256(f"{region}\n{area}\n{query}".encode()).hexdigest()


class GeocodeCache:
    """Geocoder answers in one sqlite file, each kept `ttl_s`."""

    def __init__(
        self,
        path: Path,
        ttl_s: float = GEOCODE_CACHE_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.ttl_s = ttl_s
        self.clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as con, con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(_SCHEMA)

    def get(self, region: str, bbox: Bbox, query: str) -> list[Candidate] | None:
        """The answer stored for `query` less than `ttl_s` ago, or None."""
        with closing(sqlite3.connect(self.path, timeout=10)) as con:
            row = con.execute(
                "SELECT created, body FROM queries WHERE key = ?", (key(region, bbox, query),)
            ).fetchone()
        if row is None or self.clock() - row[0] >= self.ttl_s:
            return None
        return [Candidate(**c) for c in json.loads(row[1])]

    def put(self, region: str, bbox: Bbox, query: str, candidates: list[Candidate]) -> None:
        """Store the answer for `query`, replacing any stored before."""
        body = json.dumps([asdict(c) for c in candidates])
        row = (key(region, bbox, query), self.clock(), body)
        with closing(sqlite3.connect(self.path, timeout=10)) as con, con:
            con.execute("INSERT OR REPLACE INTO queries VALUES (?, ?, ?)", row)
