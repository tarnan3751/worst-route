# Tests that need a built graph carry `@pytest.mark.data` (registered in pyproject.toml). They
# read the build through WORST_ROUTE_DATA, run on demand with `uv run pytest -m data`, and are
# skipped by the gate, which runs `pytest -m "not data"`. Everything else uses the fixtures
# under tests/fixtures/ and never reads data/ or reaches the network: `no_real_transport` makes
# httpx's real transports refuse every request in every test, so a test whose mock went missing
# fails instead of calling a server (Nominatim's above all).
from typing import Any, NoReturn

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx's real transports refuse every request; a `MockTransport` or an `ASGITransport`,
    which the tests use, is untouched."""

    def refuse(self: Any, request: httpx.Request) -> NoReturn:
        raise AssertionError(f"network access in a test: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
