# Desktop

Worst Route as an app a person downloads and opens (M6, `docs/PLAN.md` §7): one
installer per platform (macOS on Apple Silicon, Windows, Linux), a window with the map, the
national T3 build and Portland's ready to plan on, and the engine running in that computer's own
processes, no server of ours involved. It is the API and the frontend as the hosted site runs them
(`serve --frontend`, `docs/reference/hosting.md`), served on 127.0.0.1 in a window of the
system's own web view; nothing of the site changes for it.

| What | Where |
|---|---|
| The app: directories, manifest, server, window, browser, headless | `src/worst_route/desktop.py` (`worst-route-desktop`) |
| The basemap's file: download, resume, checksum | `src/worst_route/desktop_basemap.py` |
| The frozen app | `packaging/desktop/worst-route-desktop.spec`, `launch.py`, `entitlements.plist` |
| The Windows installer | `packaging/desktop/worst-route.iss` |
| The data the app carries | `tools/desktop_data.py` (`pack`, `stage`, `conus`), `packaging/desktop/data.json` |
| A build for this machine | `tools/build_desktop.sh` |
| The smoke test of a frozen app | `tools/desktop_smoke.py` |
| The release | `.github/workflows/release.yml` |

Out of scope: phones, the game's own mechanics (later briefs), a store listing, and signing
certificates, which are the owner's purchase (the workflow signs when they exist).

## Running it

```
uv sync --group desktop                                   # pywebview, PyInstaller and its hooks
uv run --no-sync worst-route-desktop --region portland    # a window, from a checkout
uv run --no-sync worst-route-desktop --headless --port 0 --region portland --geocoder none
```

`worst-route-desktop [--headless | --browser] [--port N] [--region R ...] [--data DIR]
[--frontend DIR] [--geocoder nominatim|none]`:

- **The window** (macOS and Windows, by default): pywebview on the system's web view (WKWebView,
  WebView2). It opens at once on a page saying the app is starting (the first start prepares the
  route engine: **The first start**), checks that the web view has WebGL 2 (MapLibre needs it), and
  loads the app from the server once it listens. A web view without WebGL 2 sends the app to the
  system's browser, and the window says so and that closing it stops the engine. Closing the window
  stops the server, whose job manager terminates the workers, and so does quitting the app (Cmd+Q,
  the app menu's Quit): AppKit ends the process then without returning from `webview.start`, but
  asks pywebview's `closing` event first, on which the app stops its server and waits (a progress
  stream still open ends at once). Downloads are allowed (the route's
  GeoJSON), links that open a new tab (the notices page, the share image) open in the system's
  browser (pywebview's way with them), and the web view's storage is kept between starts (not
  pywebview's private mode), so the basemap's tiles stay cached. On Windows, an app that finds no
  web view at all (no WebView2) opens the browser and shows a dialog whose OK stops it.
- **The browser** (Linux, and `--browser`): the server in the foreground, the system's browser
  opened on it, until Ctrl+C or the terminal closes. Linux has no window: its web views are the
  system's WebKitGTK, which the frozen app cannot carry (its helper processes are found at paths
  compiled into the library, PyGObject has no wheels, and WebGL 2 there is recent and often
  software), so the browser, which works everywhere, is the floor.
- **`--headless`**: the server alone, without the browser or the basemap's download, printing
  `Worst Route is running at <url>`, until SIGINT or SIGTERM (exit 0). `tools/desktop_smoke.py`
  drives the frozen app so; a lane runs it so from a checkout, on its own port, with
  `--geocoder none` and `WORST_ROUTE_DESKTOP_HOME` in its scratch directory.

`--region` names the regions served (repeatable; the first is the default) in place of the
manifest's, `--data` the data in place of the app's (from a checkout: `WORST_ROUTE_DATA`'s), and
`--frontend` the frontend's build (from a checkout: `frontend/dist`, `npm run build`). The port is
the app's own, 17429 (`config.DESKTOP_PORT`; `app_socket`), so the page's origin, and with it the
web view's storage (the challenges' badges, `docs/reference/frontend.md`, **Challenges**), is the
same at every start; a free one the system picks when another program may be there. On macOS and
Linux a probe connects to 127.0.0.1:17429 first (a 1 s timeout), and only a refused connection says
nothing listens there: the app then binds it with `SO_REUSEADDR`, at every start, so a start soon
after a quit binds over the last start's closed connections, which the stopping server leaves in
TIME_WAIT on its side (about 30 s on macOS, which refuses a plain bind meanwhile; Linux allows the
`SO_REUSEADDR` bind only when those connections carried the option too, hence every start sets it).
A connection accepted, a timeout (a listener whose backlog is full) or any other error gives a free
port: `SO_REUSEADDR` would let macOS bind 127.0.0.1:17429 over another program's listener on the
wildcard address and take its connections (a socket bound there but not listening drops the probe on
macOS, which times out, so the app falls back). Two races remain, both negligible: on Linux, a
program that has bound the wildcard address with `SO_REUSEADDR` (as servers usually do) but not yet
called `listen` is refused like a free port, so the app takes the port and that program's `listen`
fails with `EADDRINUSE`; and, on either, a program that starts listening on the wildcard address
between the probe and the bind, which macOS lets the app bind over (Linux refuses that bind, and the
app takes a free port). On Windows the app binds the port plainly, with no probe (a refusal on the
loopback can take seconds there) and never `SO_REUSEADDR` (which on Windows binds over a live
listener), and takes a free port when the bind fails; Windows refuses no bind for TIME_WAIT
connections, so a restart keeps the port. `--port N` names another, `--port 0` has the system pick
one, never taken twice. The port is bound before uvicorn starts.

The server is `create_app` and `create_site` as `serve` builds them (`docs/reference/api.md`,
**Running it**), in a thread of the app's process, with the local limits (budgets up to 120 s),
the job workers as processes (spawned: a frozen app's workers are its own executable started
again, which `multiprocessing.freeze_support()` at the top of `main` turns into the worker), and
one thing more: it answers only a `Host` of `127.0.0.1` or `localhost` (Starlette's
`TrustedHostMiddleware`; anything else is 400), so a web page that rebinds its own name to this
machine cannot reach the app. It listens on 127.0.0.1 only, which no firewall asks about.

## Where things live

**The data**, read only: inside the frozen app (`<bundle>/data`: on macOS `Worst
Route.app/Contents/Resources/data`), or `WORST_ROUTE_DATA` from a checkout:

```
desktop.json                       the manifest (below)
graphs/us-t3/latest, <id>/         the national T3 build: meta.json, strings.json, arrays/, geometry/
graphs/portland/latest, <id>/      Portland's all-roads build, picked for a trip inside its bbox
aux/places/                        states.parquet, states_outline.parquet, us-t3.parquet, portland.parquet
basemap/fonts/, sprites/           the basemap's labels (tools/fetch_basemap.sh --assets)
```

**The manifest**, `desktop.json` (`read_manifest`; written by `tools/desktop_data.py`):

```
{"format": 1, "regions": ["us-t3", "portland"], "builds": {"us-t3": "<id>", "portland": "<id>"},
 "basemap": {"name": "us", "url": "<where it is fetched from, or null>", "size": <bytes>,
             "sha256": "<hex>"},
 "contact": "<the contact in the User-Agent, or null>"}
```

The regions served, in order; the basemap file the app fetches (none without a `url`); the
contact, which the release build writes from its `WORST_ROUTE_CONTACT` repository variable (a
checkout's comes from the environment, as `serve`'s does). Moving the basemap to another host is
the release build's `WORST_ROUTE_BASEMAP_URL` variable, not a new data pack.

**The user's directory**, everything the app writes (`user_dir`): `~/Library/Application
Support/Worst Route` on macOS, `%LOCALAPPDATA%\Worst Route` on Windows, `$XDG_DATA_HOME/worst-route`
(`~/.local/share/worst-route`) on Linux; `WORST_ROUTE_DESKTOP_HOME` names another:

```
routes.sqlite, geocode.sqlite      the route store and the geocoder's cache
share/                             the share images
basemap/us.pmtiles, fonts/, sprites/   the basemap's file once fetched (.part while it comes), its labels
numba/                             the compiled kernels (NUMBA_CACHE_DIR)
matplotlib/                        matplotlib's font cache (MPLCONFIGDIR, built once; the Agg backend)
webview/                           WebView2's storage, its HTTP cache among it (Windows)
worst-route.log                    the log (three of 1 MB kept)
```

A job worker killed in the middle of a job (Cancel, quitting during a job, a worker whose
parent went away) leaves one empty `tmp*` directory in the system's temporary directory:
PyInstaller's matplotlib runtime hook makes one in every frozen process and removes it only
at a normal exit. The app's own is removed, and the system clears the rest on its own.

Removing the app leaves it (the Windows uninstaller too); deleting it starts the app afresh. On
macOS, WKWebView keeps its storage and cache under `~/Library/WebKit/` and `~/Library/Caches/`,
named by the app's bundle identifier, `io.github.tarnan3751.worst-route`.

## The first start

The engine's Numba kernels compile on a computer's first start, into `numba/`, and matplotlib
builds its font cache into `matplotlib/` the first time a share image is drawn; later starts load
both. `NUMBA_CACHE_DIR`, `MPLBACKEND` and `MPLCONFIGDIR` are set before either package is imported
(`desktop.py` imports neither at its top), and the job workers inherit them; one already set is
kept, but not in a frozen app, where PyInstaller's runtime hook sets `MPLCONFIGDIR` to a new
temporary directory at every start (with it, the font cache was built again each time, and the
daily panel shows a share image at each start; now a first start's first share image takes 9.3 s,
building the cache, and the next start's 0.3 s). A frozen app needs one
thing more: its modules' code names files that do not exist, for which Numba ignores
`NUMBA_CACHE_DIR` and caches in a directory of its own choosing (`~/Library/Caches/numba` on
macOS), so the spec collects `worst_route.graph.kernels` as its source file
(`module_collection_mode`), and Numba caches beside the user's data. A frozen app's cache is
stamped with its executable's hash, so a new release compiles once again.

**Translocation.** macOS runs an app downloaded from the internet and opened where it lies (in
Downloads, or on its disk image) from a random read-only path, a new one at each launch (App
Translocation); Numba's cache is keyed by its source file's path, so such an app compiles its
kernels again at every start (and leaves another 1 MB in `numba/` each time). The starting page
then asks to move Worst Route into Applications first (`loading_page`, when the executable lies
under `/AppTranslocation/`); moved there, as its disk image invites, it runs from one path and
compiles once.

## The basemap

The map draws OpenFreeMap's vector tiles online (`WORST_ROUTE_BASEMAP=openfreemap`,
`docs/reference/api.md`, **Basemap**; its Positron style, no key, no limits, attribution to
OpenMapTiles and OpenStreetMap) until the app's own basemap is there: a Protomaps extract of the
contiguous US to zoom 11 (the CONUS polygon, `tools/desktop_data.py conus`), a release asset the
app fetches on its first start, in the background, into the user's `basemap/`
(`desktop_basemap.fetch`): its progress in the window's title, resumed where a start left it
(HTTP `Range` on `us.pmtiles.part`), checked against the manifest's size and sha256 (a mismatch
drops the part, which the next start fetches afresh) and renamed into place only whole. From the
next start the API serves it (`pmtiles`, `ApiSettings.basemap_dir` the user's `basemap/`), with
the labels' fonts and icons the app copies there from its data. OpenStreetMap's raster tiles are
never the app's: their usage policy rules out an app's default use.

Measured (2026-10-31, Protomaps' build 20261031, 139 GB, from its directories, no tile fetched):
the contiguous US to zoom 10 is 316 MB (its bbox 384 MB), to zoom 11 684 MB (836 MB), to zoom 12
1,607 MB (1,928 MB); a release asset is at most 2 GiB. Zoom 11 holds the secondary and tertiary
roads; beyond it MapLibre draws from zoom 11's tiles, without residential streets, where
OpenFreeMap's, online, go to zoom 14.

The policies, as they apply to a desktop app: every request it makes names it
(`worst-route-desktop/<version> (<project url>[; <contact>])`, the window's User-Agent too, as
OpenFreeMap asks); MapLibre fetches only the tiles in view and nothing is prefetched; the
basemap's file is ours, from our release. The geocoder is the API's proxy unchanged
(`docs/reference/api.md`, **Geocoding**): each installation is one client, a search button, at
most one request a second, answers cached 30 days. Nominatim's policy allows end-user
applications while their users are "moderate" and the sum of their traffic within its limits; a
release with many users would turn the search to another geocoder (`--geocoder`,
`docs/reference/hosting.md`, **The geocoder**).

## Building it

```
uv run python tools/desktop_data.py stage worst-route-data.tar.gz tools/out/desktop-data --sha256 <hex>
uv sync --no-dev --group desktop
tools/build_desktop.sh tools/out/desktop-data [tools/out/desktop]
```

`tools/build_desktop.sh` builds the frontend, freezes the app with PyInstaller
(`packaging/desktop/worst-route-desktop.spec`) and packages it for the machine it runs on:
`worst-route-<version>-macos-arm64.dmg` (`Worst Route.app` and a link to Applications, LZMA),
`worst-route-<version>-windows-x64.exe` (Inno Setup, installed for the user alone into
`%LOCALAPPDATA%\Programs\Worst Route`, a Start menu entry), `worst-route-<version>-linux-x86_64.tar.gz`
(the app's folder; `./worst-route-desktop` from a terminal). PyInstaller does not cross-compile:
each platform builds on its own. The spec:

- one folder, not one file (nothing unpacked at each start), the app's code and dependencies by
  PyInstaller's analysis and the hooks (hooks-contrib's for numba, llvmlite, shapely, pyproj,
  pyarrow and uvicorn; pywebview's own), without the pipeline's packages (geopandas, OSMnx,
  osmium, networkx), the test and type tools, pywebview's other platforms, and on Linux pywebview;
- `worst_route.graph.kernels` as its source too (**The first start**);
- the data, the frontend's build, and `THIRD-PARTY-LICENSES.txt`: the licence of every
  distribution the app ships (those whose top-level packages PyInstaller's analysis collected, by
  `importlib.metadata.packages_distributions`, and PyInstaller, whose bootloader it runs) and
  Python's;
- on macOS `Worst Route.app` (bundle identifier `io.github.tarnan3751.worst-route`, macOS 12 at
  least), signed ad hoc by PyInstaller, or by `MACOS_SIGNING_IDENTITY` with the hardened runtime's
  exceptions Numba needs (`entitlements.plist`: JIT, unsigned executable memory, library
  validation off); on Windows a windowed executable, signed with `WINDOWS_CERTIFICATE` when there
  is one; on Linux a console one.

**Its data.** `tools/desktop_data.py pack OUT.tar.gz [--basemap FILE --basemap-url URL]`, run
by a person on the machine with the builds, writes what the app serves of each region's latest
build (not its `qa/` or `stages/`), the places index's files, the basemap's labels and the
manifest, and prints the pack's sha256; `stage` checks a pack and unpacks it for a build, into a
directory that is empty or not there (`--replace` deletes what one holds first). The
pack of `us-t3` (281a455e3d15) and Portland (a453e105582b): 607 MB unpacked, 262 MB packed, in
26 s. A release's pack is a release asset a person uploads (`gh release create data-<date>
--prerelease <pack>`; agents never upload), which `packaging/desktop/data.json` names by tag,
asset and sha256; the basemap's file likewise, cut by `tools/fetch_basemap.sh us --build
<YYYYMMDD> --region conus.geojson --maxzoom 11 --assets` and named by the pack's `--basemap`.

## The smoke test

`tools/desktop_smoke.py APP [--home DIR] [--budget 10] [--long]` starts a frozen app headless on a
free port, its user directory fresh (a first start) or `--home`'s (a second), and checks what a
release must do: `/api/ready` with every check passing, `/api/regions` the regions of the app's
own manifest in their order (its `desktop.json`, in the `.app`'s `Contents/Resources/data/` or in
`_internal/data/` beside the executable; a pack missing one fails), then
Powell's to Reed College and Portland to Bend (and New York to Los Angeles with `--long`), each
picked (`POST /regions/pick`), planned at seed 1 to its budget, its route validated in the
document and again by `POST /routes/{id}/validate`, the app's and its workers' resident memory
sampled meanwhile (macOS, Linux); then it stops the app (SIGTERM, exit 0; on Windows, whose
frozen app is windowed, by terminating it) and finds none of its processes left. It writes its
report to `tools/out/`.

## Releasing it

`.github/workflows/release.yml`, on a tag `v<version>` (the package's `__version__`, which it
checks) or by hand:

1. a draft release of the tag;
2. on `macos-15` (Apple Silicon), `windows-2025` and `ubuntu-24.04`: the app's environment
   (`uv sync --frozen --no-dev --group desktop`), the data pack `data.json` names, downloaded and
   checked, staged with the repository's `WORST_ROUTE_BASEMAP_URL` and `WORST_ROUTE_CONTACT`; the
   signing certificates when the secrets hold them; `tools/build_desktop.sh`; the smoke test twice
   (a first start, then a second on the same user directory); notarization on macOS when the
   Apple account's secrets exist; the installer and its sha256 attached to the draft (run by hand,
   the run's artifacts, kept a day);
3. the owner reads the draft and publishes it.

No Intel Mac build: Numba publishes no Intel macOS wheels since 0.63 (the project has 0.68), and
Intel Macs use the hosted site. The repository is public (the owner, 2026-11-01), so its release
assets download without a token: the public downloads the installers and the basemap's file from its
releases, the manifest's basemap address a variable's change (`WORST_ROUTE_BASEMAP_URL`) once the
first release holds the file.

## Opening it the first time

The app is not signed yet (the certificates are the owner's purchase), so each system warns once:

- **macOS** (Apple Silicon, 12 or later): open the disk image and drag Worst Route into
  Applications; open it, and when macOS says it cannot check it for malicious software, choose
  Done, then System Settings, Privacy & Security, and "Open Anyway" beside Worst Route, and open
  it again (macOS 15 dropped the Control-click way round). Once signed and notarized it opens at
  once.
- **Windows** (10 or 11, x64; WebView2, which Windows 11 has, for the window): run the installer;
  SmartScreen's "Windows protected your PC" has "More info", then "Run anyway". No administrator is
  needed.
- **Linux** (x86_64, glibc 2.28 or later): unpack the archive and run
  `worst-route-desktop/worst-route-desktop` from a terminal; it opens the browser.
- **Intel Macs**: the hosted site.

## Measured

On the dev machine (Apple M5, 10 cores, macOS 26.5.1), 2026-10-31, the data pack of `us-t3`
(281a455e3d15) and Portland (a453e105582b), PyInstaller 6.22.3 and hooks-contrib 2026.9, while
other lanes kept the load average at 5–25:

- **The build**: `tools/build_desktop.sh` in 211–424 s, most of it the disk image's LZMA. `Worst
  Route.app` is 1.0 GB: the data 607 MB (`us-t3`'s served files 560 MB, Portland's 17 MB, the
  places indexes 31 MB) and the app 400 MB (llvmlite 123 MB, pyarrow 119, scipy 32, pandas 18,
  pyproj 18, libpython 17, matplotlib 15); the disk image 322–340 MB (zlib's would be 443 MB, a
  zip 447 MB). `THIRD-PARTY-LICENSES.txt` holds the 59 distributions shipped and Python.
- **Starting** (headless, to `/api/health` answering, both builds loaded): a first start 4.0–16.5 s
  (Numba compiling, its cache 1.1 MB; the slowest the first run of a freshly built app, whose
  libraries macOS checks then, and an earlier probe's took 22 s), a second 1.7–3.4 s (5.2 s at a
  load average of 19).
- **Planning** (`tools/desktop_smoke.py --budget 30 --long`, seed 1, eight runs of each trip):
  Powell's to Reed College on Portland's build 65.31–65.55× (504–506 km, converging in about
  20 s); Portland to Bend on `us-t3` 10.47–11.58× (2,684–2,968 km); New York to Los Angeles on
  `us-t3` 3.91–4.66× (17,318–20,645 km), runs at the same seed differing as runs bounded by seconds
  do under another load; every route validated in its document and again by `/validate`; the app
  stopped with exit 0 each time, none of its processes left.
- **Memory** (resident, sampled while each job ran, the builds' memory-mapped pages counted, which
  the processes share and the system takes back under pressure): the app's process 146–1,060 MB,
  a job's worker 193–240 MB on Portland and 512–1,072 MB on `us-t3`.
- **The daily route** (`GET /daily`, `docs/reference/api.md`, **The daily route**): the frozen
  app planned 2026-10-31's, Sageville, IA to Acworth, GA, in 25 s on its first call, and stored
  it as route `a076702bbc25`, the route the servers with this build plan.
- **The window** (pywebview 6.2.1 on WKWebView): from a checkout, WebGL 2.0 on the "Apple GPU",
  OpenFreeMap's Positron drawn under the route with the app's attribution, the app's User-Agent
  in `navigator.userAgent`; the progress stream drew Powell's to Reed growing (98 `progress`
  events, two snapshots, `done` at 65.0× in a 10 s budget) and New York to Los Angeles on `us-t3`
  (36 `progress`, `done` at 4.3× in 30 s). The frozen app, driven through its own start, loaded
  the app 5.3 s after launch on a first start, streamed Powell's to Reed (97 `progress` events,
  `done`) and drew it at 64.0× over Positron, "Today's route" above the stops; quit by its Quit
  menu's `terminate:` (Cmd+Q) with the stream open, it stopped its server ("Application shutdown
  complete" 2.6 s later) and left no process.
- **The basemap's download**, a 30 MB stand-in over a local HTTP server with byte ranges
  (Starlette's static files): cut off at 10 MB, resumed with `Range: bytes=10485760-`, checked and
  renamed into place; a wrong sha256 refused, the part dropped; the app's User-Agent on every
  request.
- **Extract sizes**: **The basemap**.

## Limitations

- **A shared link names this computer**: the share section's link is `http://127.0.0.1:17429/`,
  which another person cannot open, and this one only while the app runs. The share image and the
  GeoJSON work.
- **The storage is the port's**: the web view's storage (the challenges' badges) belongs to the
  origin, `http://127.0.0.1:17429`; a start that finds another program may be on the port (its
  probe answered, or not refused) runs on another one, whose storage is that start's own (the
  badges then start empty, and are back at the next start on the app's port). The route store is
  the user's directory's, whatever the port.
- **The basemap switches at the next start**, not while the app runs: the server names its
  basemap when it starts.
- **The day's route is planned on this computer**: the frontend asks for it as it opens, so the
  first start of each day runs a job of 5,000 attempts on `us-t3` (25 s on the dev machine) in a
  worker beside the user's own.
- **Quitting from the Dock or by logging out** goes through the same `closing` event as Cmd+Q;
  a process killed outright (Force Quit, `kill -9`) stops nothing, and its job workers end at
  their next look at it (each accepted splice) or with their job's time limit.
- **Two apps at once** each start a server; fetching the basemap's file at once, their bytes would
  interleave, which the checksum refuses (and the next start fetches it again).
- **Untested here**: the window on Windows (WebView2's WebGL 2 and the progress stream; the app
  falls back to the browser when it lacks WebGL 2) and its dialog, the Linux bundle, the
  GeoJSON's download and the links in the window, the release workflow itself (its YAML parsed
  and its paths checked; no tag built yet), and signing and notarization, which need the owner's
  certificates; the workflow builds and smoke-tests Windows and Linux.
