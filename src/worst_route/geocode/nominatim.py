"""Nominatim's search (`docs/reference/api.md`, **Geocoding**; `docs/PLAN.md` §4.4).

`NominatimGeocoder` asks `{base_url}/search` once per query, with `format=jsonv2`, the query,
the limit, `countrycodes=us`, the bbox as `viewbox` (`<lon_min>,<lat_min>,<lon_max>,<lat_max>`)
with `bounded=1` (a filter, not a preference) and `addressdetails=0`, under the project's
User-Agent, which the usage policy requires. jsonv2 answers a list of places, each with `lat`
and `lon` as strings, `display_name` (the label here), and `category` and `type`, the main OSM
tag (joined as `amenity/cafe`). A place outside the bbox is dropped all the same. Nominatim's
429 or 403 (too many requests, or blocked) is a `GeocodeRefusedError`, after which the service
pauses; any other error status, a timeout, a transport error or a body that is not such a list
is a `GeocodeError`. Nothing is retried. The rate limit and the cache are the service's
(`service.py`): this module only asks.

The tests never let it reach the service: they hand it an `httpx.AsyncClient` on a
`MockTransport`. Without one it makes its own client, which `aclose` closes.
`user_agent(base, contact)` adds the operator's contact to the project's User-Agent.
"""

from typing import Any

import httpx

from worst_route.config import GEOCODE_TIMEOUT_S
from worst_route.geocode.base import Bbox, Candidate, GeocodeError, GeocodeRefusedError


def user_agent(base: str, contact: str | None) -> str:
    """`base` with `contact` inside its parentheses: `worst-route/0.1.0 (<url>; <contact>)`."""
    if not contact:
        return base
    if base.endswith(")"):
        return f"{base[:-1]}; {contact})"
    return f"{base} ({contact})"


class NominatimGeocoder:
    """Nominatim's `/search`, one request per query."""

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = GEOCODE_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self._owns_client = client is None
        self.client = httpx.AsyncClient() if client is None else client

    async def search(self, query: str, bbox: Bbox, limit: int) -> list[Candidate]:
        """At most `limit` places `query` may mean inside `bbox`, in Nominatim's order."""
        west, south, east, north = bbox
        params = {
            "format": "jsonv2",
            "q": query,
            "limit": str(limit),
            "countrycodes": "us",
            "viewbox": f"{west},{south},{east},{north}",
            "bounded": "1",
            "addressdetails": "0",
        }
        try:
            response = await self.client.get(
                f"{self.base_url}/search",
                params=params,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as error:
            raise GeocodeError(f"Nominatim did not answer: {error!r}") from None
        if response.status_code in (403, 429):
            raise GeocodeRefusedError(f"Nominatim answered {response.status_code}")
        if response.status_code != 200:
            raise GeocodeError(f"Nominatim answered {response.status_code}")
        try:
            places = response.json()
            if not isinstance(places, list):
                raise TypeError("the answer is not a list")
            candidates = [_candidate(place) for place in places]
        except (ValueError, KeyError, TypeError) as error:
            raise GeocodeError(f"Nominatim's answer does not parse: {error!r}") from None
        inside = [c for c in candidates if west <= c.lon <= east and south <= c.lat <= north]
        return inside[:limit]

    async def aclose(self) -> None:
        """Close the HTTP client, if it is this geocoder's own."""
        if self._owns_client:
            await self.client.aclose()


def _candidate(place: Any) -> Candidate:
    """A jsonv2 place as a candidate; KeyError, TypeError or ValueError when it is not one."""
    if not isinstance(place, dict):
        raise TypeError(f"a place is not an object: {place!r}")
    label, category, kind = place["display_name"], place["category"], place["type"]
    if not all(isinstance(v, str) for v in (label, category, kind)):
        raise TypeError(f"a place's label or tag is not text: {place!r}")
    return Candidate(label, float(place["lat"]), float(place["lon"]), f"{category}/{kind}")
