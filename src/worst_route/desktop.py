"""The desktop app (`docs/reference/desktop.md`): the API and the frontend, as `serve --frontend`
serves them, on 127.0.0.1 in a window of the system's own web view.

`main(argv)` is `worst-route-desktop`, and the frozen app's (`packaging/desktop/launch.py`). It
calls `multiprocessing.freeze_support()` first: a frozen app's job workers are the app's own
executable started again, which that call turns into the worker. Then:

- **The directories.** The data (`data_dir`) is read only: inside the frozen app
  (`<bundle>/data`), else `WORST_ROUTE_DATA`. The user's directory (`user_dir`: macOS
  `~/Library/Application Support/Worst Route`, Windows `%LOCALAPPDATA%\\Worst Route`, Linux
  `$XDG_DATA_HOME/worst-route`; `WORST_ROUTE_DESKTOP_HOME` in its place) holds what the app writes:
  the route store, the geocoder's cache and the share images beside it, the basemap it downloads,
  Numba's compiled kernels, matplotlib's font cache, WebView2's storage and the log.
  `prepare_environment` points Numba and matplotlib there before either is imported (this module
  imports neither at its top), so the first start compiles the kernels and builds the font cache
  once and later ones load them; in a frozen app over the temporary directory PyInstaller's
  runtime hook gives matplotlib, which would build the font cache again at every start.
- **The manifest.** `<data>/desktop.json` (`read_manifest`; `tools/desktop_data.py` writes it)
  names the regions served, the first the default, the basemap's file to fetch (`BasemapAsset`; no
  URL, none), and the contact for the User-Agent, which the release build writes from its
  `WORST_ROUTE_CONTACT` (a source checkout's comes from the environment, as `serve`'s does).
- **The server.** `build_site` is `create_app` and `create_site` over those: the geocoder is
  Nominatim through the API's proxy (one request a second, a search button) under the app's own
  User-Agent, `worst-route-desktop/<version> (<url>[; contact])`; the basemap is the downloaded
  Protomaps file when it is there (`choose_basemap`), else OpenFreeMap's; and only a `Host` of
  127.0.0.1 or localhost is answered, so a web page that rebinds its name to this machine cannot
  reach it. `LocalServer` runs it under uvicorn in a thread, on the app's own port
  (`app_socket`: `config.DESKTOP_PORT`, so the page's origin, and with it the web view's storage,
  is the same at every start; one the system picks when another program may be there, or the one
  `--port` names), and stops it, the job manager terminating the workers.
- **The window** (macOS and Windows, `run_window`): pywebview on the system's web view (WKWebView,
  WebView2), first a page saying the app is starting (when macOS runs the app translocated, from a
  new place each start, `loading_page` asks to move it into Applications first, and the page stays
  up while the kernels compile, as they then do at every start), then the app once the server
  listens (a first start's kernels compile after it, in the API's thread, `api/app.py`), if the web
  view has WebGL 2, which MapLibre needs; without it the app opens in the system's browser and the
  window says so (closing it stops the engine), and without a web view at all (no WebView2) it opens
  there too, a dialog standing for the window on Windows (`message_box`). Downloads are allowed (the
  route's GeoJSON), and the web view's storage is kept between starts (WebView2's in the user's
  directory, WKWebView's its own), so the basemap's tiles stay cached. The window's title shows the
  basemap's download. Closing the window, or quitting the app (Cmd+Q, after which `webview.start`
  never returns), stops the server: pywebview's `closing` event comes before either.
- **The browser** (Linux, and `--browser`, `run_browser`): the server in the foreground, the
  system's browser opened on it, until Ctrl+C (or the terminal closes). `--headless` is the same
  without the browser or the download: what `tools/desktop_smoke.py` drives.

The basemap's file is fetched in the background (`desktop_basemap.fetch`), resumed where a
previous start left it, checked against the manifest's sha256, and used from the next start.
"""

import argparse
import contextlib
import importlib
import json
import logging
import logging.handlers
import multiprocessing
import os
import signal
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Literal

from worst_route import __version__
from worst_route.desktop_basemap import BasemapAsset, fetch, install_assets, tiles_path

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.applications import Starlette

    from worst_route.config import BackendName, BasemapName, GeocoderName

log = logging.getLogger(__name__)

APP_NAME = "Worst Route"
PRODUCT = "worst-route-desktop"  # the User-Agent's product
MANIFEST = "desktop.json"
HOSTS = ("127.0.0.1", "localhost")  # the only `Host`s the server answers
Mode = Literal["window", "browser", "headless"]

LOADING = """<!doctype html><meta charset="utf-8"><body style="font:15px system-ui,sans-serif;
margin:48px;color:#1f2328;background:#f7f7f5"><h2>Worst Route</h2><p>Starting&hellip;</p>"""
TRANSLOCATED = """<p style="color:#b42318">Move Worst Route to your Applications folder first:
opened from where it was downloaded, macOS runs it from a new place each time, and it prepares its
engine again at every start.</p>"""
NO_WEBGL2 = """<!doctype html><meta charset="utf-8"><body style="font:15px system-ui,sans-serif;
margin:48px;color:#1f2328;background:#f7f7f5"><h2>Worst Route</h2><p>This computer's web view
cannot draw the map (it has no WebGL 2), so Worst Route opened in your web browser at
<a href="{url}">{url}</a>.</p><p>Keep this window open while you play: closing it stops the
engine.</p>"""
FAILED = """<!doctype html><meta charset="utf-8"><body style="font:15px system-ui,sans-serif;
margin:48px;color:#1f2328;background:#f7f7f5"><h2>Worst Route could not start</h2><p>{error}</p>
<p style="color:#6b7280">The log is {log}.</p>"""
WEBGL2 = """(() => { try { return !!document.createElement("canvas").getContext("webgl2"); }
catch (e) { return false; } })()"""


@dataclass(frozen=True)
class Manifest:
    """What the app's data holds: the regions it serves (the first the default), the basemap's
    file to fetch, and the contact for the User-Agent."""

    regions: tuple[str, ...]
    basemap: BasemapAsset | None = None
    contact: str | None = None


def user_dir(
    platform: str = sys.platform, environ: Mapping[str, str] = os.environ, home: Path | None = None
) -> Path:
    """Where the app writes: the platform's per-user application data, or
    `WORST_ROUTE_DESKTOP_HOME`."""
    if environ.get("WORST_ROUTE_DESKTOP_HOME"):
        return Path(environ["WORST_ROUTE_DESKTOP_HOME"])
    home = home or Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if platform == "win32":
        local = environ.get("LOCALAPPDATA")
        return (Path(local) if local else home / "AppData" / "Local") / APP_NAME
    data_home = environ.get("XDG_DATA_HOME")
    return (Path(data_home) if data_home else home / ".local" / "share") / "worst-route"


def bundle_dir() -> Path | None:
    """The frozen app's own files (PyInstaller's `sys._MEIPASS`), None from a checkout."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))  # noqa: B009 (no attribute outside a frozen app)
    return None


def data_dir() -> Path:
    """The data served, read only: the frozen app's `data/`, else `WORST_ROUTE_DATA`."""
    bundle = bundle_dir()
    if bundle is not None:
        return bundle / "data"
    from worst_route.config import settings

    return settings().data_dir


def frontend_dir() -> Path:
    """The frontend's build: the frozen app's `frontend/`, else the checkout's `frontend/dist`."""
    bundle = bundle_dir()
    if bundle is not None:
        return bundle / "frontend"
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def prepare_environment(
    user: Path, environ: MutableMapping[str, str] = os.environ, frozen: bool | None = None
) -> None:
    """Point Numba's cache and matplotlib's (Agg only) into `user`, unless already set; before
    either is imported, and inherited by the job workers. A frozen app's MPLCONFIGDIR is always
    `user`'s: PyInstaller's runtime hook sets it to a new temporary directory at each start."""
    if frozen is None:
        frozen = bundle_dir() is not None
    environ.setdefault("NUMBA_CACHE_DIR", str(user / "numba"))
    environ.setdefault("MPLBACKEND", "Agg")
    if frozen:
        environ["MPLCONFIGDIR"] = str(user / "matplotlib")
    else:
        environ.setdefault("MPLCONFIGDIR", str(user / "matplotlib"))


def translocated(executable: str = sys.executable) -> bool:
    """Whether macOS runs the app translocated: an app opened where it was downloaded runs from a
    random path each time, so Numba's cache, keyed by the path, is compiled again at every start."""
    return "/AppTranslocation/" in executable


def loading_page(executable: str = sys.executable) -> str:
    """The window's first page; asking to move the app into Applications when it is
    translocated."""
    if translocated(executable):
        return LOADING + TRANSLOCATED
    return LOADING


def read_manifest(data: Path) -> Manifest:
    """`<data>/desktop.json`; `FileNotFoundError` naming it when it is not there."""
    path = data / MANIFEST
    if not path.is_file():
        raise FileNotFoundError(f"no {MANIFEST} in {data}: pass --region, or use the app's data")
    raw = json.loads(path.read_text())
    basemap = raw.get("basemap")
    asset = None
    if basemap is not None:
        name, size, sha256 = basemap["name"], basemap["size"], basemap["sha256"]
        asset = BasemapAsset(name, basemap.get("url"), size, sha256)
    return Manifest(tuple(raw["regions"]), asset, raw.get("contact") or None)


def choose_basemap(directory: Path, asset: BasemapAsset | None) -> "BasemapName":
    """The downloaded Protomaps file when it is there (it is renamed into place only once its
    checksum holds), else OpenFreeMap's tiles."""
    if asset is not None and tiles_path(directory, asset).is_file():
        return "pmtiles"
    return "openfreemap"


def choose_mode(headless: bool, browser: bool, platform: str = sys.platform) -> Mode:
    """`headless`, the browser (asked for, or on Linux, whose web views the app does not
    bundle), else the window."""
    if headless:
        return "headless"
    if browser or platform.startswith("linux"):
        return "browser"
    return "window"


def free_socket(host: str = "127.0.0.1", port: int = 0) -> socket.socket:
    """A socket bound to `host` on `port` (0: one the system picks, never taken twice), for
    uvicorn to listen on."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, port))
    return sock


def app_socket(
    port: int | None, preferred: int | None = None, platform: str = sys.platform
) -> socket.socket:
    """The socket the app listens on: on `port` when one is given (0: one the system picks), else
    on `preferred` (`config.DESKTOP_PORT`), so the page's origin is the same at every start, unless
    another program may be there (then on a free one).

    On macOS and Linux a probe connects first: only a refused connection says nothing listens
    there, and the port is then bound with SO_REUSEADDR, every time, so a start soon after a quit
    binds over the last start's closed connections (TIME_WAIT; Linux allows it only when those
    carried the option too); a connection accepted, a timeout or any other error gives a free port,
    since SO_REUSEADDR would let macOS bind over another program's listener on the wildcard
    address (a socket bound but not listening drops the probe there, which times out). Two races
    remain, both negligible: on Linux, a program that has bound the wildcard address with
    SO_REUSEADDR (as servers do) but not called `listen` yet is refused like a free port, and the
    app takes the port, the program's `listen` then failing (EADDRINUSE); and a program that starts
    listening on the wildcard address between the probe and the bind, which macOS lets the app
    bind over (Linux refuses the bind, and the app takes a free port). On Windows a plain bind, no
    probe (refusals there can take seconds) and never SO_REUSEADDR (which would bind over a live
    listener), a free port when it fails: TIME_WAIT blocks no bind there."""
    if port is not None:
        return free_socket(port=port)
    if preferred is None:
        from worst_route.config import DESKTOP_PORT

        preferred = DESKTOP_PORT
    windows = platform == "win32"
    if windows or _refused(preferred):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if not windows:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", preferred))
            return sock
        except OSError:
            sock.close()
    log.info("port %d is taken; listening on a free one, with storage of its own", preferred)
    return free_socket()


def _refused(port: int) -> bool:
    """Whether a connection to 127.0.0.1:`port` is refused, which only a port without a listener
    does."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except ConnectionRefusedError:
        return True
    except OSError:
        return False
    return False


def user_agent(contact: str | None) -> str:
    """The app's User-Agent, `worst-route-desktop/<version> (<url>[; contact])`, sent to
    Nominatim, to the basemap's host and, from the window, to OpenFreeMap."""
    from worst_route.config import user_agent_of
    from worst_route.geocode.nominatim import user_agent as with_contact

    return with_contact(user_agent_of(PRODUCT), contact)


def build_site(
    data: Path,
    user: Path,
    regions: Sequence[str],
    *,
    manifest: Manifest,
    frontend: Path,
    contact: str | None,
    geocoder: "GeocoderName" = "nominatim",
    backend: "BackendName | None" = None,
) -> tuple["Starlette", "FastAPI"]:
    """The site (the frontend at `/`, the API under `/api`) and its API, over `data`'s builds of
    `regions`, writing into `user`."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from worst_route.api.app import ApiSettings, create_app
    from worst_route.api.static import create_site
    from worst_route.config import DEFAULT_BACKEND, user_agent_of

    basemap = user / "basemap"
    settings = ApiSettings(
        data_dir=data,
        builds=tuple((region, "latest") for region in regions),
        backend=backend or DEFAULT_BACKEND,
        store_path=user / "routes.sqlite",
        geocoder=geocoder,
        user_agent=user_agent_of(PRODUCT),
        contact=contact,
        basemap=choose_basemap(basemap, manifest.basemap),
        basemap_name=manifest.basemap.name if manifest.basemap else None,
        basemap_dir=basemap,
    )
    api = create_app(settings)
    site = create_site(api, frontend)
    site.add_middleware(TrustedHostMiddleware, allowed_hosts=list(HOSTS))
    return site, api


class LocalServer:
    """uvicorn serving `site` on `sock`, in a thread of this process."""

    def __init__(self, site: "Starlette", sock: socket.socket) -> None:
        import uvicorn

        config = uvicorn.Config(
            site,
            log_config=None,  # the app's logging (`setup_logging`)
            access_log=False,
            proxy_headers=False,
            timeout_graceful_shutdown=5,  # an open progress stream does not hold the exit
        )
        self.server = uvicorn.Server(config)
        self.sock = sock
        self.url = "http://{}:{}/".format(*sock.getsockname()[:2])
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [sock]}, name="server", daemon=True
        )

    def start(self, timeout_s: float = 120.0) -> None:
        """Start it and wait until it listens; `RuntimeError` if it stops or never does."""
        self.thread.start()
        deadline = time.monotonic() + timeout_s
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the server did not start (see the log)")
            time.sleep(0.05)

    def stop(self, timeout_s: float = 30.0) -> None:
        """Ask it to stop (its lifespan stops the job manager and its workers) and wait; again,
        nothing more. A progress stream still open (the window's, before it closes) ends at once:
        sse-starlette's own watch for uvicorn's exit looks at the signal handlers, which a server
        in a thread does not install, and would leave it to the 5 s graceful shutdown."""
        from sse_starlette.sse import AppStatus

        self.server.should_exit = True
        if self.thread.is_alive():
            AppStatus.should_exit = True
            try:
                self.thread.join(timeout_s)
            finally:
                AppStatus.should_exit = False  # process-wide: a later server's streams would end
        self.sock.close()


def setup_logging(user: Path, console: bool) -> Path:
    """Log to `<user>/worst-route.log` (three of 1 MB kept), and to stderr when there is one and
    `console`; the path."""
    path = user / "worst-route.log"
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3)
    ]
    if console and sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.getLogger("worst_route.access").setLevel(logging.WARNING)  # each tile's request
    return path


def start_download(
    manifest: Manifest, user: Path, agent: str, report: Callable[[str], None]
) -> threading.Thread | None:
    """Fetch the manifest's basemap file in the background, unless it is there or has no URL,
    saying how it goes through `report`; its fonts and icons come from the app's data."""
    asset = manifest.basemap
    directory = user / "basemap"
    if asset is None or not asset.url or tiles_path(directory, asset).is_file():
        return None
    shown = -1

    def progress(have: int, size: int) -> None:
        nonlocal shown
        percent = int(100 * have / size) if size else 100
        if percent != shown:
            shown = percent
            report(f"downloading the offline map, {percent} % of {size / 1e6:,.0f} MB")

    def run() -> None:
        try:
            fetch(asset, directory, agent, progress)
        except Exception as error:  # whatever it was, the next start resumes it
            log.warning("the basemap did not download: %s", error)
            report("the offline map did not download: it is tried again at the next start")
        else:
            report("the offline map is ready: it is used from the next start")

    thread = threading.Thread(target=run, name="basemap", daemon=True)
    thread.start()
    return thread


def run_window(build: Callable[[], LocalServer], manifest: Manifest, user: Path, agent: str) -> int:
    """The app in a window of the system's web view; the browser when it has no WebGL 2, or
    when there is no web view to open."""
    try:
        import webview
    except Exception as error:  # no web view to open: the browser is the floor
        log.warning("no web view (%s): opening the browser", error)
        return run_browser(build, manifest, user, agent, hold=_hold())

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        APP_NAME, html=loading_page(), width=1400, height=900, min_size=(900, 600)
    )
    assert window is not None  # None only when an `initialized` handler cancels it: none here
    servers: list[LocalServer] = []

    def stop_servers() -> None:
        """Before the window closes, or the app quits: Cmd+Q ends the process from AppKit, never
        returning from `webview.start`, but asks `closing` first (on the GUI's thread, waited
        for)."""
        for server in servers:
            server.stop()

    window.events.closing += stop_servers

    def title(text: str) -> None:
        window.set_title(f"{APP_NAME} · {text}")

    def started() -> None:  # pywebview's thread, once the window is up
        try:
            server = build()
            servers.append(server)
            server.start()
            from worst_route.graph.backends import backend_name  # the server's already

            if translocated() and backend_name() == "csr":
                # its kernels compile at every start: its page (move the app) stays up meanwhile
                importlib.import_module("worst_route.graph.kernels")  # waits on the API's import
        except Exception as error:  # shown in the window, and logged
            log.exception("the app did not start")
            log_file = _escape(str(user / "worst-route.log"))
            page = FAILED.format(error=_escape(str(error)), log=log_file)
            window.load_html(page)
            return
        window.events.loaded.wait(30)
        try:
            webgl2 = bool(window.evaluate_js(WEBGL2))
        except Exception:  # a web view that cannot answer cannot draw either
            webgl2 = False
        if webgl2:
            window.load_url(server.url)
        else:
            log.warning("the web view has no WebGL 2: opening the browser at %s", server.url)
            webbrowser.open(server.url)
            window.load_html(NO_WEBGL2.format(url=server.url))
        start_download(manifest, user, agent, title)

    try:
        webview.start(
            started,
            private_mode=False,
            storage_path=str(user / "webview"),
            user_agent=agent,
        )
    except Exception as error:  # a web view that does not start (no WebView2): the browser
        log.warning("the web view did not start (%s): opening the browser", error)
        for server in servers:
            server.stop()
        return run_browser(build, manifest, user, agent, hold=_hold())
    for server in servers:
        server.stop()
    return 0


def run_browser(
    build: Callable[[], LocalServer],
    manifest: Manifest,
    user: Path,
    agent: str,
    open_browser: bool = True,
    hold: Callable[[str], None] | None = None,
) -> int:
    """The server in the foreground until Ctrl+C or a termination; the browser opened on it and
    the basemap fetched, unless headless. With `hold`, the server runs while `hold(url)` does (a
    dialog, for a Windows app without a window or a console to stop it from)."""
    server = build()
    if hold is not None:
        server.start()
        webbrowser.open(server.url)
        start_download(manifest, user, agent, log.info)
        hold(server.url)
        server.stop()
        return 0

    def stop(signum: int, frame: FrameType | None) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)  # uvicorn re-raises the signal it stopped on: exit 0

    def opened() -> None:
        while not server.server.started:
            if server.server.should_exit:
                return
            time.sleep(0.05)
        print(f"{APP_NAME} is running at {server.url}", flush=True)
        if open_browser:
            print("Close this window or press Ctrl+C to stop it.", flush=True)
            webbrowser.open(server.url)
            start_download(manifest, user, agent, lambda text: print(text, flush=True))

    threading.Thread(target=opened, name="opened", daemon=True).start()
    with contextlib.suppress(KeyboardInterrupt):  # uvicorn re-raises the Ctrl+C it stopped on
        server.server.run(sockets=[server.sock])
    server.sock.close()
    return 0


def _hold() -> Callable[[str], None] | None:
    """What keeps a windowless app running: the dialog on Windows; elsewhere the foreground."""
    return message_box if sys.platform == "win32" else None


def message_box(url: str) -> None:
    """Windows: a dialog saying where the app is open, its OK stopping it."""
    import ctypes

    text = f"{APP_NAME} is open in your web browser at {url}\n\nClick OK to stop it."
    getattr(ctypes, "windll").user32.MessageBoxW(None, text, APP_NAME, 0x40)  # noqa: B009


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=PRODUCT, description="Worst Route on this computer.")
    parser.add_argument("--headless", action="store_true", help="the server only (for the smoke)")
    parser.add_argument("--browser", action="store_true", help="open the browser, not a window")
    parser.add_argument(
        "--port", type=int, help="the port (0: a free one; default: the app's own, else a free one)"
    )
    parser.add_argument("--region", action="append", help="a region to serve (repeatable)")
    parser.add_argument("--data", type=Path, help="the data directory (default: the app's)")
    parser.add_argument("--frontend", type=Path, help="the frontend's build (default: the app's)")
    parser.add_argument(
        "--geocoder", choices=("nominatim", "none"), default="nominatim", help="the place search"
    )
    parser.add_argument("--version", action="version", version=f"{PRODUCT} {__version__}")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """`worst-route-desktop`: the app in a window, the browser, or headless."""
    multiprocessing.freeze_support()  # a frozen app's job worker runs here, and exits
    args = parse(argv)
    mode = choose_mode(args.headless, args.browser)
    user = user_dir()
    user.mkdir(parents=True, exist_ok=True)
    prepare_environment(user)
    setup_logging(user, console=mode != "window")
    data = args.data or data_dir()
    manifest = read_manifest(data) if args.region is None else Manifest(tuple(args.region))
    if manifest.contact is None:
        from worst_route.config import settings

        manifest = replace(manifest, contact=settings().contact)
    agent = user_agent(manifest.contact)
    log.info("%s %s: data %s, regions %s, %s", PRODUCT, __version__, data, manifest.regions, mode)

    def build() -> LocalServer:
        install_assets(data / "basemap", user / "basemap")  # a first start's, once the window is up
        site, _ = build_site(
            data,
            user,
            manifest.regions,
            manifest=manifest,
            frontend=args.frontend or frontend_dir(),
            contact=manifest.contact,
            geocoder=args.geocoder,
        )
        return LocalServer(site, app_socket(args.port))

    if mode == "window":
        return run_window(build, manifest, user, agent)
    return run_browser(build, manifest, user, agent, open_browser=mode == "browser")
