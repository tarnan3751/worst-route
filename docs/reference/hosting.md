# Hosting

How worst-route runs for the public on one machine (M5.4, `docs/PLAN.md` §7, §4.5, §5.1): a
container serving the built frontend and the API from one process, the data mounted beside it,
Protomaps' vector basemap instead of OpenStreetMap's raster tiles, each client's share of the
jobs and the place searches, JSON logs with request ids, a readiness check, the attribution, and
a reverse proxy in front for TLS. Locally nothing changes: `worst-route serve` without its public
options is the server it was, and the CLI has no limits.

| What | Where |
|---|---|
| The image | `Dockerfile`, `.dockerignore` |
| One machine's service | `compose.yaml` |
| The frontend and the API from one process | `src/worst_route/api/static.py` (`serve --frontend`) |
| Each client's share, the public budget | `src/worst_route/api/limits.py`, the job manager (`docs/reference/api.md`, **Public limits**) |
| Request ids, JSON logs | `src/worst_route/api/logs.py` (`docs/reference/api.md`, **Logs**) |
| `/config`, the basemap's files | `src/worst_route/api/basemap_api.py` (`docs/reference/api.md`, **Basemap**) |
| `/ready` | `src/worst_route/api/ready_api.py` (`docs/reference/api.md`, **Endpoints**) |
| The basemap, fetched by a person | `tools/fetch_basemap.sh` |
| The basemap, drawn | `frontend/src/map/basemap.ts` (`docs/reference/frontend.md`, **Basemap**) |

Out of scope: TLS inside the container (the reverse proxy terminates it), more than one machine,
autoscaling, and a geocoder other than Nominatim (**The geocoder**).

## The image

```
docker build -t worst-route .                                     # this machine's architecture
docker buildx build --platform linux/amd64 -t worst-route .       # a VPS's, from an Apple machine
docker run -d -p 127.0.0.1:8000:8000 -v "$PWD/data:/data:ro" -v worst-route-state:/state \
  worst-route serve --region portland --host 0.0.0.0 --port 8000 --store /state/routes.sqlite \
  --frontend /app/frontend --public --log-format json --trust-proxy
docker compose up -d --build                                       # the same, from compose.yaml
```

Bound to `127.0.0.1`, the container is reached only through a reverse proxy on the same machine
(**Behind a reverse proxy**), so it runs with `--trust-proxy` and believes that proxy's
`X-Forwarded-For`; the image's own command leaves it out, so a container exposed directly never
takes a client's word for its address.

Three stages: `node:22-bookworm-slim` installs the frontend's dependencies (`npm ci`) and builds
`dist/`; `python:3.12-slim-bookworm` with uv 0.13.1 installs the Python environment from
`uv.lock` (`uv sync --frozen --no-dev`, the package itself not editable); the last stage, the
same Python image, holds that environment in `/app/.venv` and the build in `/app/frontend`,
runs as the user `worst-route` (uid 10001), and builds matplotlib's font cache once, so the
first share image does not. It never contains data (`.dockerignore` leaves out `data/`, the
tests, the docs and the tools).

Its command is `worst-route serve --region portland --host 0.0.0.0 --port 8000 --store
/state/routes.sqlite --frontend /app/frontend --public --log-format json`: the site
(`docs/reference/api.md`, **Running it**: the frontend at `/`, the API under `/api`), the
public limits, JSON lines on stdout. Another region, `--trust-proxy` behind a proxy or
`--geocoder none` go in the command (`compose.yaml` gives it whole). Its environment:
`WORST_ROUTE_DATA=/data`, `WORST_ROUTE_BASEMAP=pmtiles` (the vector basemap; `raster` would
send every visitor to OpenStreetMap's tile servers, which their policy rules out for a public
site), `NUMBA_CACHE_DIR=/state/numba` (the compiled kernels; the package's own directory is not
writable), `MPLCONFIGDIR` under the user's home; `WORST_ROUTE_CONTACT` is the operator's, for the
geocoder's User-Agent. Its health check asks `/api/ready` every 30 s (after a 60 s start), so the
container is unhealthy until its data is whole.

Built on the dev machine (Docker 29.8, linux/arm64): about a minute once the base images are
pulled; 1.57 GB, most of it the Python environment (the pipeline's dependencies, osmium, OSMnx,
geopandas, pyarrow, numba, come with it, though the server uses only some of them).

## The volumes

```
/data  (read only)
  graphs/<region>/<build_id>/   a build: worst-route pipeline … save (docs/reference/pipeline.md)
  graphs/<region>/latest        the build served: its id
  aux/places/                   the places index: tools/fetch_census.sh, worst-route aux places
  basemap/<region>.pmtiles      the basemap: tools/fetch_basemap.sh
  basemap/fonts/, sprites/      its labels' fonts and icons: tools/fetch_basemap.sh --assets
/state (written by the server)
  routes.sqlite                 the route store
  geocode.sqlite                the geocoder's cache
  share/                        the share images
  numba/                        the compiled kernels
```

`compose.yaml` mounts `./data` read only and keeps `/state` in a named volume, `state`. A build
is a directory: one made on another machine is copied with its `latest` file (the server loads
the build `latest` names when it starts, and refuses to start without one). A new build of the
region (`pipeline save` writes a new id, or `--force` replaces one and changes its
`graph_version`) is served after a restart, and `/ready` fails until then once the files of the
loaded build have changed on disk; routes stored on a build that is no longer there answer 409
`BUILD_UNAVAILABLE` (`docs/reference/api.md`).

## Deploying

A person, on a machine with the repository (the dev machine), then on the server:

1. **The build.** `uv run worst-route pipeline download --region R`, then `filter`, `build`,
   `cleanup`, `enrich`, `tiers`, `save` and `qa` (`CLAUDE.md`, **Commands**;
   `docs/reference/pipeline.md`). It needs far more memory than serving does (**Sizing**), so
   it runs on the dev machine and the build is copied.
2. **The places index.** `tools/fetch_census.sh <FIPS…>` for the states the region touches, then
   `uv run worst-route aux places --region R` (`docs/reference/presentation.md`, **Places**).
3. **The basemap.** Install go-pmtiles (`brew install pmtiles`, or a release from
   https://github.com/protomaps/go-pmtiles/releases), find the latest Protomaps build
   (**The basemap**), look at the extract's size with `--dry-run`, then cut it with its labels:
   `tools/fetch_basemap.sh R --build YYYYMMDD --bbox W,S,E,N --assets` (or `--region
   area.geojson` for a shape such as the US).
4. **Copy** `data/graphs/R/`, `data/aux/places/` and `data/basemap/` to the server's `data/`.
5. **Run** `docker compose up -d --build` beside it, the region set in `compose.yaml`'s command
   and `WORST_ROUTE_CONTACT` in the environment (an address Nominatim's operators can write to).
6. **The proxy** in front, with TLS (**Behind a reverse proxy**).
7. **Check** `curl https://<host>/api/ready`: `ready`, with the build, the store, the places
   index and the basemap (with labels) each `ok`; `docker compose logs -f` shows the JSON lines.

## The basemap

**Protomaps' builds.** Protomaps publishes a planet build of OpenStreetMap as one PMTiles file a
day, `https://build.protomaps.com/YYYYMMDD.pmtiles`, zooms 0–15, about 139 GB (the build of
2026-10-30), listed with their sizes at `https://build-metadata.protomaps.dev/builds.json`
(oldest first; its `key` is the file name) and kept about a week. The latest:

```
curl -s https://build-metadata.protomaps.dev/builds.json \
  | python3 -c 'import json,sys; print(max(b["key"] for b in json.load(sys.stdin)))'
```

They are an Open Database License Produced Work: OpenStreetMap must be credited
(**Attribution**). Protomaps' documentation discourages pointing a site at them: an area is cut
from one and served from your own machine, which is what `tools/fetch_basemap.sh` does.

**`tools/fetch_basemap.sh NAME --build YYYYMMDD (--bbox W,S,E,N | --region FILE | --planet)
[--maxzoom Z] [--dry-run] [--assets]`**, run by a person, never the gate or an agent:

- With `--bbox` or `--region` (a GeoJSON polygon), `pmtiles extract <build URL> … --bbox=…` reads
  the area's tiles from the build over HTTP byte ranges, down to `--maxzoom` (the build's 15
  when left out; MapLibre draws deeper zooms from the deepest it has), into
  `$WORST_ROUTE_DATA/basemap/NAME.pmtiles` (default the repository's `data/basemap/`), NAME the
  region the API serves. The script never changes directory, so a relative `--region` file or
  `WORST_ROUTE_DATA` is the caller's. The file is written to a temporary name and renamed once
  whole, so a failed extract leaves the previous one. `--dry-run` asks `pmtiles` for the
  extract's tiles and size without downloading them, and writes nothing, not even the
  directory. A flag without its value is refused with the usage (exit 2). `--planet` downloads the whole build instead (resumable with curl), which
  is a planet's size for a US site.
- `--assets` fetches the labels' fonts and icons from `protomaps/basemaps-assets` at a pinned
  commit (`028c18f713baecad011301ff7a69acc39bcc2ae7`, the head of `main` on 2026-10-31; a sparse
  checkout, git's hashes checking what arrives): the four fonts the labels ask for (Noto Sans
  Regular, Medium and Italic, and Noto Sans Devanagari Regular v1, each 256 files of glyphs,
  about 11.5 MB together) into `basemap/fonts/` with their licence (`OFL.txt`, the SIL Open Font
  License), and the icons of the grayscale flavor (`grayscale.json`, `.png` and their `@2x`)
  into `basemap/sprites/`. Without them the map draws the basemap without labels.
- It needs `pmtiles` (or `$PMTILES`) to cut an area, curl for `--planet`, git for `--assets`;
  `$PROTOMAPS_BUILDS`, `$BASEMAP_ASSETS_REPO` and `$BASEMAP_ASSETS_COMMIT` point it elsewhere
  (its tests use them). A missing `--build`, area or tool is refused before anything runs.

How large an extract is depends on its area and its deepest zoom, and `--dry-run` says so; none
was downloaded while this was built, so no size is recorded here.

**Serving.** The API serves `<data>/basemap/` under `/api/basemap/` with byte ranges and a day's
caching (`docs/reference/api.md`, **Basemap**), so the single container needs nothing else. A
proxy in front may serve the directory itself (below), which spares the API's process the
reads.

## Behind a reverse proxy

The container listens on the machine's own port 8000 (`compose.yaml`); a reverse proxy
terminates TLS and passes everything on. nginx:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name worst-route.example.org;
    ssl_certificate     /etc/letsencrypt/live/worst-route.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/worst-route.example.org/privkey.pem;
    client_max_body_size 64k;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
        proxy_buffering off;        # the progress stream (SSE) goes out as it comes
        proxy_read_timeout 300s;    # the stream pings every 15 s; a job runs at most 60 s + 30 s
    }

    # Optional: the basemap's files straight from the disk (byte ranges included).
    location /api/basemap/ {
        alias /srv/worst-route/data/basemap/;
        add_header Cache-Control "public, max-age=86400";
    }
}
```

With it, the service runs with `--trust-proxy` (as `compose.yaml` has it): the client a limit
counts is then the last address of `X-Forwarded-For`, the one nginx appends
(`$proxy_add_x_forwarded_for` keeps whatever the client sent before it, which is never
believed), and nginx's `$request_id` becomes the request's id in the logs. Without
`--trust-proxy` behind a proxy, every visitor is the proxy and shares one client's share; with
it but no proxy, a visitor could name any address. The API's stream already sends
`X-Accel-Buffering: no`. Caddy does the same with `reverse_proxy 127.0.0.1:8000` (it sets
`X-Forwarded-For` and streams event streams unbuffered).

## Limits, logs, readiness

- **The public limits** (`docs/reference/api.md`, **Public limits**): budgets up to 60 s (120
  locally), the leash at most 150 km as everywhere, and for each client one job running and three
  waiting (`TOO_MANY_JOBS` with its counts) and ten place searches a minute (`RATE_LIMITED` with
  `retry_after_s`). They are per process and per address; there is no authentication.
- **The logs** (`docs/reference/api.md`, **Logs**): one JSON object a line on stdout (`docker
  compose logs`), each request's line with its id (`X-Request-ID` in the answer), its path
  without the query, its status, its duration and the client's salted hash, never an address
  or a place searched for; a job's lines with its id, its end with its outcome and route.
- **`/api/ready`**: 200 when the build, the route store, the places index and the basemap are
  there, else 503 naming what is missing; `/api/health` only says the process lives.

## Sizing

Measured so far (2026-10-31), on the dev machine (Apple M5, 10 cores, 24 GB):

- **Oregon** (build 5913ade7a2df; `docs/reference/pipeline.md`, **Oregon**): 237,581 nodes and
  593,555 edges, a 174 MB build on disk. Building it took 378 s at a load average of 20, with a
  2.58 GB peak in cleanup: a job for the dev machine, not the server. Served, the API's process
  takes 170–300 MB once the build is loaded and 445–455 MB once it has drawn a share image; a
  job's worker 374–399 MB of resident memory, mostly the build's pages, which the workers share
  through the page cache. `GET /routes/{id}` answers in 0.6–0.9 s for a 4,117 km route of 5,335
  steps, an uncached share image in 1.1 s.
- **Portland in the container** (build 4b8998deee9f, the image above, Docker Desktop's VM):
  the API's process 365 MB resident with two jobs running, their workers 229–234 MB each, the
  container 471 MiB in all; a job's first event after its `snapped` (a fresh worker, its
  imports, numba's kernels, the seed route) about 1 s, the same with numba's cache on a fresh
  volume as with one kept.

So a server needs about 0.5 GB for the API, 0.2–0.4 GB a running worker (`--workers`, by default
the cores less two: in a container that is the host's or the VM's cores), and room in the page
cache for the build (and for the basemap file, which is read a range at a time). The plan's
targets for the national step stand as estimates (`docs/PLAN.md` §7 M5.4): an 8–16 GB VPS for a
T3 national build, 32 GB for all roads. Both are shrinking: the compact build format (version 4,
its arrays about 38 % of today's) and the array-based pipeline are under way, so these numbers
are today's.

## The geocoder

The place search asks Nominatim through the API's proxy (`docs/reference/api.md`,
**Geocoding**): one request a second for the whole server, answers cached 30 days, the
project's User-Agent with `WORST_ROUTE_CONTACT`, and on a public server ten searches a minute a
client. That keeps to Nominatim's usage policy for light use. For more, the geocoder is behind
the `Geocoder` protocol (`geocode/base.py`, `search(query, bbox, limit)`), so another is a new
implementation and a `--geocoder` choice:

- **Photon**, self-hosted: Java and OpenSearch over a prebuilt index (the US alone is tens of
  GB of disk), no per-request cost, an operator's machine to keep.
- **A paid geocoder** with a free tier: no machine to keep, a key and a per-request price.

Which, and when, is open (`docs/project_overview.md` §3.2). Until then `--geocoder none` turns
the search off, and `--nominatim-url` points the proxy at a self-hosted Nominatim.

## Attribution

- **On the map**, in MapLibre's attribution control, bottom right and not collapsed: "©
  OpenStreetMap contributors" with the raster basemap, "Protomaps © OpenStreetMap" with the
  vector one, linked to openstreetmap.org/copyright (the OSMF's attribution guideline; the
  basemap is an ODbL Produced Work). The strings are the app's own: a tileset's metadata is never
  shown, so no third party's HTML reaches the attribution control.
- **Under the panel**, a footer: "Road data © OpenStreetMap contributors (ODbL) · Towns and
  states: U.S. Census Bureau", linked: the routes are OpenStreetMap's roads, the towns and states
  the Census Bureau's boundaries.
- **On the share image**, its footer: "worst-route · © OpenStreetMap contributors (ODbL) ·
  Boundaries: U.S. Census Bureau" (`docs/reference/presentation.md`, **Share image**).
- The labels' fonts are under the SIL Open Font License, whose text `--assets` copies beside
  them.

## Tests

The hosting pieces are tested with the API's (`docs/reference/api.md`, **Tests**:
`test_logs.py`, `test_limits.py`, `test_ready_api.py`, `test_basemap_api.py`,
`test_static.py`, and `tests/unit/test_cli.py` for `serve`'s options), the frontend's
(`docs/reference/frontend.md`, **Tests**: `map/basemap.test.ts`, `api/types.test.ts`'s
`/config`), and `tests/unit/test_fetch_basemap.py`, which runs `tools/fetch_basemap.sh` against
a stand-in for `pmtiles` on `PATH` and a local git repository standing for the assets'. The gate
builds no image; `docker build` and a run are checked by hand (**The image**).

## Limitations

- **One process**, as the API is (`docs/reference/api.md`, **Limitations**): the limits, the
  jobs and the geocoder's rate are per process, and a restart forgets running jobs.
- **Per address**: a client with many addresses has many shares; an IPv6 client is counted by
  its /64.
- **The image carries the pipeline's dependencies**, which the server does not need.
- **`tools/fetch_basemap.sh` has run only against stand-ins**: no build was downloaded while it
  was written, so an extract's size and time are not measured, and the frontend's labels were
  drawn only from stand-in icons without fonts (`docs/reference/frontend.md`, **Measured**).
