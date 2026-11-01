"""The geocoding endpoint (`docs/reference/api.md`, **Geocoding**; `docs/PLAN.md` §4.4).

    GET /geocode?q=<text>&region=<region>    the places the text may mean in the region

The query is normalised (`geocode.base.normalise`) and refused when it is then empty or longer
than `GEOCODE_MAX_QUERY_CHARS` (422 `INVALID_REQUEST`, `field: "q"`); then the region is
checked as `routes_api` checks it (422 `UNKNOWN_REGION`), and a server whose geocoder is `none`
refuses (503 `GEOCODER_DISABLED`). On a public server a client (`limits.client_of`) then
searches at most `PUBLIC_GEOCODE_PER_MINUTE` times in any minute, a search the cache answers
counted too (429 `RATE_LIMITED`, + `retry_after_s`, `limits.GeocodeQuota`). The service
(`geocode.service`, on the app's state) answers from its cache or asks Nominatim in its turn of
the rate limit: 429 `RATE_LIMITED` (+
`retry_after_s`) when the queue is full, 502 `GEOCODER_ERROR` when Nominatim fails, or refused
within the last minute (the pause). A missing `region` is FastAPI's own `INVALID_REQUEST`,
before any of these.
"""

from dataclasses import asdict

from fastapi import APIRouter, Request

from worst_route.api.limits import client_of
from worst_route.api.routes_api import ApiError, api_of, bbox_of, region_of
from worst_route.api.schemas import GeocodeCandidateOut, GeocodeOut
from worst_route.config import GEOCODE_MAX_QUERY_CHARS
from worst_route.geocode.base import GeocodeError, normalise
from worst_route.geocode.limiter import RateLimited

router = APIRouter()

BUSY = "Place search is busy. Try again in a few seconds."


@router.get("/geocode")
async def geocode(request: Request, region: str, q: str = "") -> GeocodeOut:
    """The places `q` may mean in `region`, best first."""
    query = normalise(q)
    if not query:
        raise ApiError(422, "INVALID_REQUEST", "the query is empty", field="q")
    if len(query) > GEOCODE_MAX_QUERY_CHARS:
        message = f"the query is over {GEOCODE_MAX_QUERY_CHARS} characters"
        raise ApiError(422, "INVALID_REQUEST", message, field="q")
    api = api_of(request)
    bbox = bbox_of(region_of(api, region))
    if api.geocode is None:
        raise ApiError(503, "GEOCODER_DISABLED", "this server has no geocoder")
    if api.quota is not None:
        wait = api.quota.take(client_of(request.scope, api.settings.trust_proxy))
        if wait is not None:
            message = f"at most {api.quota.per_minute} searches a minute; try again in {wait} s"
            raise ApiError(429, "RATE_LIMITED", message, retry_after_s=wait)
    try:
        answer = await api.geocode.search(region, bbox, query)
    except RateLimited as busy:
        raise ApiError(429, "RATE_LIMITED", BUSY, retry_after_s=busy.retry_after_s) from None
    except GeocodeError as error:
        raise ApiError(502, "GEOCODER_ERROR", str(error)) from None
    return GeocodeOut(
        query=answer.query,
        region=region,
        candidates=[GeocodeCandidateOut(**asdict(c)) for c in answer.candidates],
        cached=answer.cached,
    )
