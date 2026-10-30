"""Helpers for tests that build the geocoder's HTTP client without a real transport."""

from typing import Any

import httpx
import pytest


def offline_clients(monkeypatch: pytest.MonkeyPatch) -> list[httpx.AsyncClient]:
    """Make `httpx.AsyncClient(...)` without a transport build its client on a `MockTransport`
    that fails every request, so code that makes its own client (a `NominatimGeocoder` given
    none, the app's default service) builds no real one; the clients made so, in order. A
    client given its transport (a test's `ASGITransport`) is made as asked."""
    real = httpx.AsyncClient
    made: list[httpx.AsyncClient] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"a request in a test: {request.method} {request.url}")

    def make(**kwargs: Any) -> httpx.AsyncClient:
        if "transport" in kwargs:
            return real(**kwargs)
        client = real(transport=httpx.MockTransport(refuse), **kwargs)
        made.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make)
    return made
