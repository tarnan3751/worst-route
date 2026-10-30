"""The HTTP API: the only contract between the backend and the frontend (`docs/reference/api.md`).

`app` builds the FastAPI application (`create_app`), `routes_api` holds the endpoints and
`geocode_api` the geocoding one (over the `geocode` package), `schemas` the request and
response shapes, `jobs` the job manager, `worker` what runs in a job's process, `sse` the event
payloads and their stream, and `store` the sqlite route store.
This package's `__init__` imports nothing, so a spawned worker process loads only `worker` and
the engine, never FastAPI.
"""
