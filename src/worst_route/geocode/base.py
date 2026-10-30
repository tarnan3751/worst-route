"""What a geocoder is (`docs/reference/api.md`, **Geocoding**; `docs/PLAN.md` §4.4).

A `Geocoder` turns a free-form query into at most `limit` `Candidate`s inside a bbox (min lon,
min lat, max lon, max lat), best first: `NominatimGeocoder` (`nominatim.py`), or
`FakeGeocoder` (`fake.py`) in the tests. A failure upstream (an error status, a timeout, a body
that does not parse) is a `GeocodeError`; a refusal (too many requests, or blocked), after
which the service pauses, is a `GeocodeRefusedError`. `normalise` gives the query as the proxy
validates, sends and caches it: casefolded, each run of whitespace one space, stripped.
"""

from dataclasses import dataclass
from typing import Protocol

Bbox = tuple[float, float, float, float]  # min lon, min lat, max lon, max lat


@dataclass(frozen=True)
class Candidate:
    """A place a query may mean: its label, where it is, and its main OSM tag (`amenity/cafe`)."""

    label: str
    lat: float
    lon: float
    category: str


class GeocodeError(Exception):
    """The geocoder could not answer."""


class GeocodeRefusedError(GeocodeError):
    """The geocoder refused to answer: too many requests (429), or blocked (403)."""


class Geocoder(Protocol):
    """A place search."""

    async def search(self, query: str, bbox: Bbox, limit: int) -> list[Candidate]:
        """At most `limit` places `query` may mean inside `bbox`, best first."""
        ...

    async def aclose(self) -> None:
        """Release what the geocoder holds (its HTTP client)."""
        ...


def normalise(query: str) -> str:
    """`query` casefolded, each run of whitespace one space, stripped."""
    return " ".join(query.casefold().split())
