"""`GET /ready`: 200 when the deployment is whole, 503 naming what is missing."""

import json
from pathlib import Path

import pytest

from ..presentation.support import write_fixture_places
from .hosting import hosted

pytestmark = pytest.mark.anyio


def checks_of(body: dict[str, object]) -> dict[str, bool]:
    return {c["name"]: c["ok"] for c in body["checks"]}  # type: ignore[attr-defined,index]


async def test_ready_with_the_build_the_store_and_the_places_index(data: Path) -> None:
    write_fixture_places(data)
    async with hosted(data) as (http, _):
        answer = await http.get("/ready")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["status"] == "ready"
    assert checks_of(body) == {"build": True, "store": True, "places": True, "basemap": True}
    assert body["checks"][0]["detail"] == "portland/grid"
    assert str(data) not in answer.text  # no path on the server


async def test_not_ready_without_the_places_index(data: Path) -> None:
    async with hosted(data) as (http, _):
        answer = await http.get("/ready")
    assert answer.status_code == 503
    body = answer.json()
    assert body["status"] == "not_ready" and checks_of(body)["places"] is False
    [places] = [c for c in body["checks"] if c["name"] == "places"]
    assert "worst-route aux places --region portland" in places["detail"]


async def test_not_ready_once_the_build_is_replaced_or_gone(data: Path) -> None:
    write_fixture_places(data)
    meta = data / "graphs" / "portland" / "grid" / "meta.json"
    async with hosted(data) as (http, _):
        replaced = {**json.loads(meta.read_text()), "graph_version": "f" * 64}
        meta.write_text(json.dumps(replaced))
        answer = await http.get("/ready")
        assert answer.status_code == 503 and checks_of(answer.json())["build"] is False
        assert answer.json()["checks"][0]["detail"] == (
            "the build portland/grid was replaced on disk: restart to serve it"
        )
        meta.unlink()
        answer = await http.get("/ready")
        assert answer.status_code == 503 and checks_of(answer.json())["build"] is False
        assert answer.json()["checks"][0]["detail"] == (
            "the build portland/grid is gone from the data directory: restore it, or restart"
        )
        assert str(data) not in answer.text and "No such file" not in answer.text
        assert (await http.get("/health")).status_code == 200  # alive all the same
