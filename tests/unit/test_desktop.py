"""The desktop app's pieces that run everywhere (`worst_route.desktop`): its directories, its
environment, its manifest, the basemap and the mode it picks, the port, and its server started,
guarded and stopped with a job's worker running. The window itself, and the frozen app, are the
release workflow's (`tools/desktop_smoke.py`), not the gate's."""

import http.client
import json
import multiprocessing
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from worst_route import desktop
from worst_route.desktop import Manifest
from worst_route.desktop_basemap import BasemapAsset

from ..api.support import GRID, GRID_BBOX, save_build
from ..engine.graphs import at_node, lattice

HOME = Path("/home/someone")
ASSET = BasemapAsset("us", "https://example.org/us.pmtiles", 3, "ab" * 32)


def test_the_user_directory_is_the_platforms_own() -> None:
    assert desktop.user_dir("darwin", {}, HOME) == (
        HOME / "Library" / "Application Support" / "Worst Route"
    )
    local = {"LOCALAPPDATA": "C:/Users/someone/AppData/Local"}
    assert desktop.user_dir("win32", local, HOME) == Path(local["LOCALAPPDATA"]) / "Worst Route"
    assert desktop.user_dir("win32", {}, HOME) == HOME / "AppData" / "Local" / "Worst Route"
    assert desktop.user_dir("linux", {}, HOME) == HOME / ".local" / "share" / "worst-route"
    xdg = {"XDG_DATA_HOME": "/data/someone"}
    assert desktop.user_dir("linux", xdg, HOME) == Path("/data/someone/worst-route")
    own = {"WORST_ROUTE_DESKTOP_HOME": "/tmp/lane", **xdg}
    assert desktop.user_dir("darwin", own, HOME) == Path("/tmp/lane")


def test_the_data_and_the_frontend_are_the_frozen_apps_or_the_checkouts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from worst_route.config import settings

    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path / "data"))
    settings.cache_clear()
    try:
        assert desktop.bundle_dir() is None
        assert desktop.data_dir() == tmp_path / "data"
        checkout = Path(desktop.__file__).resolve().parents[2]
        assert desktop.frontend_dir() == checkout / "frontend" / "dist"
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
        assert desktop.data_dir() == tmp_path / "bundle" / "data"
        assert desktop.frontend_dir() == tmp_path / "bundle" / "frontend"
    finally:
        settings.cache_clear()


def test_numba_and_matplotlib_write_into_the_user_directory(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    desktop.prepare_environment(tmp_path, environ)
    assert environ == {
        "NUMBA_CACHE_DIR": str(tmp_path / "numba"),
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(tmp_path / "matplotlib"),
    }
    mine = {"NUMBA_CACHE_DIR": "/elsewhere", "MPLCONFIGDIR": "/mine"}
    desktop.prepare_environment(tmp_path, mine, frozen=False)
    assert mine["NUMBA_CACHE_DIR"] == "/elsewhere"  # one already set is kept
    assert mine["MPLCONFIGDIR"] == "/mine"
    # Frozen, PyInstaller's runtime hook has set MPLCONFIGDIR to a new temporary directory before
    # the app's code runs: the user's replaces it, so the font cache is built once, not each start.
    hooked = {"MPLCONFIGDIR": "/var/folders/xy/T/_MEI123456"}
    desktop.prepare_environment(tmp_path, hooked, frozen=True)
    assert hooked["MPLCONFIGDIR"] == str(tmp_path / "matplotlib")


def test_the_loading_page_asks_a_translocated_app_to_move() -> None:
    installed = "/Applications/Worst Route.app/Contents/MacOS/Worst Route"
    moved = (
        "/private/var/folders/xy/abc/T/AppTranslocation/0F1E2D3C-4B5A-6978-8796-A5B4C3D2E1F0/d/"
        "Worst Route.app/Contents/MacOS/Worst Route"
    )
    assert desktop.translocated(moved) and not desktop.translocated(installed)
    assert desktop.loading_page(installed) == desktop.LOADING
    page = desktop.loading_page(moved)
    assert page.startswith(desktop.LOADING) and "Applications folder first" in page


def test_the_manifest_names_the_regions_the_basemap_and_the_contact(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no desktop.json in"):
        desktop.read_manifest(tmp_path)
    basemap = {"name": "us", "url": ASSET.url, "size": 3, "sha256": ASSET.sha256}
    manifest = {"regions": ["us-t3", "portland"], "basemap": basemap, "contact": "a@b.org"}
    (tmp_path / "desktop.json").write_text(json.dumps(manifest))
    assert desktop.read_manifest(tmp_path) == Manifest(("us-t3", "portland"), ASSET, "a@b.org")
    # Before the release's basemap has an address, and without a contact.
    bare = {"regions": ["portland"], "basemap": {**basemap, "url": None}, "contact": ""}
    (tmp_path / "desktop.json").write_text(json.dumps(bare))
    read = desktop.read_manifest(tmp_path)
    assert read.basemap is not None and read.basemap.url is None and read.contact is None


def test_the_basemap_is_the_downloaded_file_once_it_is_there(tmp_path: Path) -> None:
    assert desktop.choose_basemap(tmp_path, None) == "openfreemap"
    assert desktop.choose_basemap(tmp_path, ASSET) == "openfreemap"
    (tmp_path / "us.pmtiles.part").write_bytes(b"ab")  # half way: not yet
    assert desktop.choose_basemap(tmp_path, ASSET) == "openfreemap"
    (tmp_path / "us.pmtiles").write_bytes(b"abc")
    assert desktop.choose_basemap(tmp_path, ASSET) == "pmtiles"


def test_the_window_on_macos_and_windows_the_browser_on_linux() -> None:
    assert desktop.choose_mode(False, False, "darwin") == "window"
    assert desktop.choose_mode(False, False, "win32") == "window"
    assert desktop.choose_mode(False, False, "linux") == "browser"
    assert desktop.choose_mode(False, True, "darwin") == "browser"
    assert desktop.choose_mode(True, False, "linux") == "headless"


def test_the_user_agent_names_the_app_its_version_and_the_contact() -> None:
    from worst_route import __version__

    url = "https://github.com/tarnan3751/worst-route"
    assert desktop.user_agent(None) == f"worst-route-desktop/{__version__} ({url})"
    assert desktop.user_agent("a@b.org") == f"worst-route-desktop/{__version__} ({url}; a@b.org)"


def test_a_free_port_on_the_loopback_never_taken_twice() -> None:
    first, second = desktop.free_socket(), desktop.free_socket()
    try:
        assert first.getsockname()[0] == "127.0.0.1" and first.getsockname()[1] > 0
        assert first.getsockname()[1] != second.getsockname()[1]
    finally:
        first.close()
        second.close()


def a_free_port() -> int:
    sock = desktop.free_socket()
    port: int = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.skipif(sys.platform == "win32", reason="Windows blocks no bind on TIME_WAIT")
def test_the_apps_own_port_when_free_and_again_soon_after_a_quit() -> None:
    from worst_route.config import DESKTOP_PORT

    assert DESKTOP_PORT == 17429
    port = a_free_port()
    sock = desktop.app_socket(None, preferred=port)  # free: the app's own, with SO_REUSEADDR
    assert sock.getsockname() == ("127.0.0.1", port)
    assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0
    sock.listen()
    client = socket.create_connection(("127.0.0.1", port))
    served, _ = sock.accept()
    served.close()  # the server closes first, as a stopping app does: TIME_WAIT on its side
    client.recv(1)
    client.close()
    sock.close()
    with pytest.raises(OSError), socket.socket() as plain:
        plain.bind(("127.0.0.1", port))  # what a plain bind makes of TIME_WAIT
    again = desktop.app_socket(None, preferred=port)  # a start soon after a quit
    assert again.getsockname() == ("127.0.0.1", port)
    again.close()


def test_another_programs_listener_on_the_wildcard_gives_a_free_port() -> None:
    port = a_free_port()
    with socket.socket() as other:
        other.bind(("0.0.0.0", port))
        other.listen()
        sock = desktop.app_socket(None, preferred=port)  # the probe connects: not the app's
        assert sock.getsockname()[1] not in (0, port)
        sock.close()


def test_a_probe_that_is_not_refused_gives_a_free_port(monkeypatch: pytest.MonkeyPatch) -> None:
    def hung(*args: object, **kwargs: object) -> socket.socket:
        raise TimeoutError("timed out")  # a listener whose backlog is full

    port = a_free_port()
    monkeypatch.setattr(desktop.socket, "create_connection", hung)
    sock = desktop.app_socket(None, preferred=port)
    assert sock.getsockname()[1] not in (0, port)
    sock.close()


def test_on_windows_a_plain_bind_without_a_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("no probe on Windows")

    monkeypatch.setattr(desktop.socket, "create_connection", probe)
    port = a_free_port()
    sock = desktop.app_socket(None, preferred=port, platform="win32")
    assert sock.getsockname() == ("127.0.0.1", port)
    assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
    sock.listen()
    other = desktop.app_socket(None, preferred=port, platform="win32")  # the bind fails
    assert other.getsockname()[1] not in (0, port)
    other.close()
    sock.close()


def test_the_port_named_or_one_the_system_picks() -> None:
    port = a_free_port()
    named = desktop.app_socket(port)  # --port names it
    assert named.getsockname() == ("127.0.0.1", port)
    named.close()
    picked = desktop.app_socket(0)  # --port 0: one the system picks
    assert picked.getsockname()[1] > 0
    picked.close()


def local(
    port: int, path: str, body: dict[str, Any] | None = None, host: str | None = None
) -> tuple[int, Any]:
    """A request to the server under test on this machine's loopback (stdlib: httpx's real
    transports refuse every request in the tests, `tests/conftest.py`), its status and body."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers = {"Host": host} if host else {}
    if body is None:
        connection.request("GET", path, headers=headers)
    else:
        headers["Content-Type"] = "application/json"
        connection.request("POST", path, json.dumps(body), headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    kind = response.getheader("content-type", "")
    return response.status, json.loads(data) if kind.startswith("application/json") else data


@pytest.mark.slow
@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is POSIX's")
def test_headless_it_serves_until_terminated_and_exits_0(tmp_path: Path) -> None:
    from ..presentation.support import write_fixture_places

    data = tmp_path / "data"
    save_build(data, GRID, "grid", bbox=GRID_BBOX)
    write_fixture_places(data)
    (data / "desktop.json").write_text(json.dumps({"regions": ["portland"]}))
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html><title>Worst Route</title>")
    argv = ["--headless", "--port", "0", "--data", str(data), "--frontend", str(frontend)]
    code = f"import sys; from worst_route.desktop import main; sys.exit(main({argv!r}))"
    # The kernels as plain Python: this start compiles nothing (its Numba cache would be new).
    env = {**os.environ, "NUMBA_DISABLE_JIT": "1"}
    env["WORST_ROUTE_DESKTOP_HOME"] = str(tmp_path / "user")
    process = subprocess.Popen(
        [sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout is not None
        line = process.stdout.readline()  # "Worst Route is running at http://127.0.0.1:<port>/"
        assert line.startswith("Worst Route is running at http://127.0.0.1:"), line
        port = int(line.rsplit(":", 1)[1].strip(" /\n"))
        status, ready = local(port, "/api/ready")
        assert status == 200 and ready["status"] == "ready"
        assert local(port, "/api/health", host="evil.example")[0] == 400
        process.send_signal(signal.SIGTERM)
        assert process.wait(60) == 0
    finally:
        if process.poll() is None:
            process.kill()
    assert (tmp_path / "user" / "routes.sqlite").exists()
    assert (tmp_path / "user" / "worst-route.log").exists()


@pytest.mark.slow
def test_the_server_serves_the_site_to_this_machine_only_and_stops_with_its_workers(
    tmp_path: Path,
) -> None:
    huge = lattice(40, 40)  # a run of a few seconds, in a real worker process
    data = tmp_path / "data"
    lon, lat = huge.node_lon, huge.node_lat  # a bbox around it, as the API's tests give theirs
    bbox = [
        float(lon.min()) - 0.1,
        float(lat.min()) - 0.1,
        float(lon.max()) + 0.1,
        float(lat.max()) + 0.1,
    ]
    save_build(data, huge, "huge", bbox=bbox)
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html><title>Worst Route</title>")
    user = tmp_path / "user"
    site, api = desktop.build_site(
        data,
        user,
        ["portland"],
        manifest=Manifest(("portland",), ASSET),
        frontend=frontend,
        contact=None,
        geocoder="none",
        backend="python",
    )
    server = desktop.LocalServer(site, desktop.free_socket())
    server.start()
    port = server.sock.getsockname()[1]
    assert server.url == f"http://127.0.0.1:{port}/"
    try:
        status, page = local(port, "/")
        assert status == 200 and b"Worst Route" in page
        assert local(port, "/api/health")[0] == 200
        config = local(port, "/api/config")[1]
        assert config["basemap"]["kind"] == "openfreemap"  # no file downloaded yet
        assert local(port, "/api/health", host=f"localhost:{port}")[0] == 200
        # A page whose name was rebound to this machine sends its own Host: refused.
        assert local(port, "/api/health", host="evil.example")[0] == 400
        stops = [dict(zip(("lat", "lon"), at_node(huge, v), strict=True)) for v in (0, 1599)]
        body = {"region": "portland", "stops": stops, "seed": 1, "budget_s": 60}
        status, created = local(port, "/api/jobs", body)
        assert status == 202
        deadline = time.monotonic() + 60
        while local(port, f"/api/jobs/{created['job_id']}")[1]["iteration"] == 0:
            assert time.monotonic() < deadline
            time.sleep(0.1)
        assert multiprocessing.active_children()  # its worker, searching
        stream = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        stream.request("GET", f"/api{created['events_url']}")
        assert stream.getresponse().status == 200  # its progress stream, open as a window's is
    finally:
        stopping = time.monotonic()
        server.stop()
    assert time.monotonic() - stopping < 3  # the open stream ended, no 5 s graceful wait
    from sse_starlette.sse import AppStatus

    assert AppStatus.should_exit is False  # process-wide: set only while it stopped
    assert not server.thread.is_alive()
    assert multiprocessing.active_children() == []  # the job manager stopped the worker
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
        pass
    assert (user / "routes.sqlite").exists() and not (data / "routes.sqlite").exists()
    assert api.state.api.settings.basemap_dir == user / "basemap"
