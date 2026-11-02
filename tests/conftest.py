# Tests that need a built graph carry `@pytest.mark.data` (registered in pyproject.toml). They
# read the build through WORST_ROUTE_DATA, run on demand with `uv run pytest -m data`, and are
# skipped by the gate, which runs `pytest -m "not data"`. Everything else uses the fixtures
# under tests/fixtures/ and never reads data/ or reaches the network: `no_real_transport` makes
# httpx's real transports refuse every request in every test, so a test whose mock went missing
# fails instead of calling a server (Nominatim's above all). `broken_numba` stands for a numba
# installed but broken, for the tests of what a backend's name resolves to.
import sys
from pathlib import Path
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


@pytest.fixture
def broken_numba(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """A numba installed but broken: a package of that name first on the path, whose import raises
    `ImportError` (as numba's does under a NumPy it was not built for)."""
    root: Path = tmp_path_factory.mktemp("broken")
    (root / "numba").mkdir()
    (root / "numba" / "__init__.py").write_text('raise ImportError("numba needs another NumPy")\n')
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.delitem(sys.modules, "numba", raising=False)
