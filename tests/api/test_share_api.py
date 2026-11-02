"""`GET /routes/{id}/share.png` over httpx in process: the PNG, rendered once for concurrent
first requests and then served from the cache, with and without a places index, once per theme;
HEAD; unknown and malformed ids; unknown themes; a route whose build is gone; a render that
fails; and the app importing without matplotlib."""

import asyncio
import os
import struct
import subprocess
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from worst_route.api.app import Api, create_app
from worst_route.api.jobs import JobSettings
from worst_route.api.store import RouteStore
from worst_route.engine.io import route_document
from worst_route.engine.plan import plan_route
from worst_route.presentation import share_image
from worst_route.presentation.share_palettes import PALETTES

from ..presentation.support import write_fixture_places
from .support import PORTLAND, SLOW, api_input, api_settings, loaded, request, save_build

pytestmark = pytest.mark.anyio


def store_route(
    data: Path, build_id: str = "grid", objective: str = "distance", **document: Any
) -> str:
    """Plan a route on a lattice build for `objective` and store it (with `document`'s fields
    changed)."""
    build = loaded(data, build_id)
    req = request(build.data, [0, 99], seed=3, max_iterations=200, objective=objective)
    result = plan_route(build.data, req, backend=build.backend)
    doc = {**route_document(build.data, result), **document}
    store = RouteStore(data / "routes.sqlite")
    return store.put(doc, input=api_input(req), baseline_edges=result.baseline.edges)


@asynccontextmanager
async def serving(data: Path) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    config = api_settings(
        data,
        builds=PORTLAND,
        backend="python",
        jobs=JobSettings(inline_workers=True),
        geocoder="none",
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


@pytest.mark.parametrize("with_places", [True, False])
async def test_the_image_renders_once_then_comes_from_the_cache(
    data: Path, monkeypatch: pytest.MonkeyPatch, with_places: bool
) -> None:
    if with_places:
        write_fixture_places(data)
    route_id = store_route(data)
    renders: list[str] = []
    real = share_image.render_share_image

    def counted(*args: Any, **kwargs: Any) -> bytes:
        renders.append(next(t for t, p in PALETTES.items() if p == kwargs["palette"]))
        return real(*args, **kwargs)

    # The endpoint imports the drawing when it renders, so it finds this stand-in there.
    monkeypatch.setattr(share_image, "render_share_image", counted)
    async with serving(data) as (client, app):
        api: Api = app.state.api
        assert (api.places["portland"] is not None) == with_places
        url = f"/routes/{route_id}/share.png"
        first, second = await asyncio.gather(client.get(url), client.get(url))
        for r in (first, second):
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/png"
            assert r.headers["cache-control"] == "public, max-age=86400"
        assert first.content == second.content
        assert struct.unpack(">II", first.content[16:24]) == (1200, 630)
        assert renders == ["classic"]  # two first requests, one render, in today's colours
        cached = data / "share" / f"{route_id}-classic-v3.png"
        assert cached.read_bytes() == first.content
        assert sorted(p.name for p in cached.parent.iterdir()) == [cached.name]
        assert api.share_locks == {}
        cached.write_bytes(b"from the cache")
        for again in (await client.get(url), await client.get(f"{url}?theme=classic")):
            assert again.status_code == 200 and again.content == b"from the cache"
        assert renders == ["classic"]
        # Another theme is another image, rendered once in its own palette and kept beside it.
        night = await client.get(f"{url}?theme=night")
        assert night.status_code == 200 and night.headers["content-type"] == "image/png"
        assert night.content not in (first.content, b"from the cache")
        assert (await client.get(f"{url}?theme=night")).content == night.content
        assert renders == ["classic", "night"]
        kept = sorted(p.name for p in cached.parent.iterdir())
        assert kept == [cached.name, f"{route_id}-night-v3.png"]
        assert api.share_locks == {}


async def test_an_image_s_lock_is_kept_while_it_renders_whatever_another_theme_did(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Night's render waits while classic's first request finishes: night's lock stays known, so
    # a later request for night waits on it rather than rendering it again.
    route_id = store_route(data)
    started, release = threading.Event(), threading.Event()
    renders: list[str] = []
    real = share_image.render_share_image

    def held(*args: Any, **kwargs: Any) -> bytes:
        theme = next(t for t, p in PALETTES.items() if p == kwargs["palette"])
        renders.append(theme)
        if theme == "night":
            started.set()
            assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(share_image, "render_share_image", held)
    url = f"/routes/{route_id}/share.png"
    async with serving(data) as (client, app):
        api: Api = app.state.api
        classic = asyncio.create_task(client.get(url))
        night = asyncio.create_task(client.get(f"{url}?theme=night"))
        assert await asyncio.to_thread(started.wait, 10)
        assert (await classic).status_code == 200
        assert list(api.share_locks) == [(route_id, "night")]
        again = asyncio.create_task(client.get(f"{url}?theme=night"))
        release.set()
        first, second = await night, await again
        assert first.status_code == second.status_code == 200
        assert first.content == second.content
        assert sorted(renders) == ["classic", "night"]
        assert api.share_locks == {}


async def test_a_slowest_time_route_shows_its_time_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a lattice with signals and stop signs, whose delays part the two ratios.
    root = tmp_path / "data"
    save_build(root, SLOW, "slow")
    slowest = store_route(root, "slow", objective="time")
    longest = store_route(root, "slow")
    drawn: dict[str, tuple[str, str]] = {}
    real = share_image.headline

    def recorded(metrics: Any, objective: str) -> tuple[str, str]:
        top = real(metrics, objective)
        drawn[objective] = top
        return top

    monkeypatch.setattr(share_image, "headline", recorded)
    async with serving(root) as (client, _):
        time_doc = (await client.get(f"/routes/{slowest}")).json()["metrics"]
        distance_doc = (await client.get(f"/routes/{longest}")).json()["metrics"]
        for route_id in (slowest, longest):
            assert (await client.get(f"/routes/{route_id}/share.png")).status_code == 200
    assert time_doc["delay_s"] > 0
    assert f"{time_doc['time_ratio']:.1f}" != f"{time_doc['ratio']:.1f}"
    assert drawn == {
        "time": (f"{time_doc['time_ratio']:,.1f}×", "slower than\nit had to be"),
        "distance": (f"{distance_doc['ratio']:,.1f}×", "longer than\nit had to be"),
    }


async def test_unknown_and_malformed_ids_are_not_found(data: Path) -> None:
    async with serving(data) as (client, app):
        for route_id in ("0123456789ab", "ABCDEF012345", "0123456789abc", "x" * 12):
            r = await client.get(f"/routes/{route_id}/share.png")
            assert r.status_code == 404, route_id
            assert r.json() == {"code": "NOT_FOUND", "message": f"no route {route_id}"}
        assert not (data / "share").exists()
        assert app.state.api.share_locks == {}


async def test_an_unknown_theme_is_an_invalid_request(data: Path) -> None:
    route_id = store_route(data)
    known = ", ".join(PALETTES)
    async with serving(data) as (client, app):
        # "auto" is the frontend's setting, which it resolves before asking.
        for theme in ("neon", "Night", "auto", ""):
            r = await client.get(f"/routes/{route_id}/share.png", params={"theme": theme})
            assert r.status_code == 422, theme
            assert r.json() == {
                "code": "INVALID_REQUEST",
                "message": f"unknown theme {theme!r}; known: {known}",
                "field": "theme",
            }
        head = await client.head(f"/routes/{route_id}/share.png?theme=neon")
        assert head.status_code == 422 and head.content == b""
        # Not a route id is not found first, whatever the theme.
        malformed = await client.get("/routes/ABCDEF012345/share.png?theme=neon")
        assert malformed.status_code == 404
        assert not (data / "share").exists()
        assert app.state.api.share_locks == {}


async def test_head_gives_the_headers_without_the_image(data: Path) -> None:
    route_id = store_route(data)
    url = f"/routes/{route_id}/share.png"
    async with serving(data) as (client, _):
        head = await client.head(url)  # first: it renders, to know the length
        assert head.status_code == 200 and head.content == b""
        assert (data / "share" / f"{route_id}-classic-v3.png").exists()
        got = await client.get(url)
        for name in ("content-type", "cache-control", "content-length"):
            assert head.headers[name] == got.headers[name], name
        assert int(head.headers["content-length"]) == len(got.content)
        again = (await client.head(url)).headers  # from the cache
        assert again.pop("x-request-id") != head.headers.pop("x-request-id")  # one per request
        assert again == head.headers
        missing = await client.head("/routes/0123456789ab/share.png")
        assert missing.status_code == 404


async def test_a_route_whose_build_is_gone_is_unavailable(data: Path) -> None:
    route_id = store_route(data, graph_version="another build")
    async with serving(data) as (client, app):
        r = await client.get(f"/routes/{route_id}/share.png")
        assert r.status_code == 409 and r.json()["code"] == "BUILD_UNAVAILABLE"
        assert not (data / "share").exists()
        assert app.state.api.share_locks == {}


async def test_a_render_that_fails_leaves_no_file_and_no_lock(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route_id = store_route(data)
    url = f"/routes/{route_id}/share.png"

    def broken(*args: Any, **kwargs: Any) -> bytes:
        raise RuntimeError("the drawing failed")

    async with serving(data) as (client, app):
        with monkeypatch.context() as patched:
            patched.setattr(share_image, "render_share_image", broken)
            with pytest.raises(RuntimeError, match="the drawing failed"):
                await client.get(url)
        assert app.state.api.share_locks == {}
        assert not (data / "share").exists()
        r = await client.get(url)  # the next request draws it
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"


async def test_two_writers_of_one_image_leave_one_file_and_no_part(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two servers on one store (their locks are their own) render the same route at once; each
    # writes its part before either renames (a barrier in os.replace), as a race could have it.
    route_id = store_route(data)
    rendered = iter([b"\x89PNG first", b"\x89PNG second"])
    monkeypatch.setattr(share_image, "render_share_image", lambda *a, **k: next(rendered))
    both = threading.Barrier(2, timeout=10)
    real = os.replace

    def replace(src: Any, dst: Any) -> None:
        if str(src).endswith(".part"):
            both.wait()  # both parts are written by now
        real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    url = f"/routes/{route_id}/share.png"
    async with serving(data) as (one, _), serving(data) as (two, _):
        first, second = await asyncio.gather(one.get(url), two.get(url))
    assert first.status_code == second.status_code == 200
    share = data / "share"
    assert [p.name for p in share.iterdir()] == [f"{route_id}-classic-v3.png"]  # no part left
    image = share / f"{route_id}-classic-v3.png"
    assert image.read_bytes() in (b"\x89PNG first", b"\x89PNG second")


def test_the_app_imports_without_matplotlib() -> None:
    # In a fresh interpreter: the share endpoint imports the drawing only when it renders.
    code = "import sys, worst_route.api.app; print('matplotlib' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
