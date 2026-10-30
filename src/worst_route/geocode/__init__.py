"""The geocoding proxy's backend: place names to points, behind a cache and a rate limit.

`base` holds `Candidate`, the `Geocoder` protocol, `GeocodeError` and `normalise`; `nominatim`
the client for Nominatim's search; `fake` a geocoder with fixed answers for tests; `cache` the
sqlite cache of answers; `limiter` the one-request-a-second limit; `service` the order they
run in. The endpoint is `api/geocode_api.py`. How it works: `docs/reference/api.md`,
**Geocoding**; the design is `docs/PLAN.md` §4.4.
"""
