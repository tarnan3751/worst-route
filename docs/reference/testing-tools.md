# Testing and tools

How a change is checked, where agents work, and how a series lands. The conventions are stated
in `CLAUDE.md`, **Agent workflow**; this file is the how-it-works.

## Tests

What exists: `tests/conftest.py`, which documents the `data` marker and whose autouse fixture
makes httpx's real transports refuse every request, so a test whose mock went missing fails
instead of reaching a server (`MockTransport` and `ASGITransport` are untouched); `tests/unit/`,
with the config and CLI tests (typer's `CliRunner`) and the desktop app's (`test_desktop.py`,
whose server, started in a thread, and `worst-route-desktop --headless`, started as a process
until its SIGTERM, it asks over the loopback with the standard library's `http.client`, and
`test_desktop_basemap.py`, the basemap's download on a `MockTransport`, and
`test_desktop_licenses.py`, the bundled libraries' licence texts against the spec's `BUNDLED`;
`docs/reference/desktop.md`); `tests/pipeline/`, with the pipeline step
tests and a `conftest.py` whose autouse fixture also refuses `httpx.stream` and whose
`data_dir` fixture points `WORST_ROUTE_DATA` at a temporary directory; `tests/graph/`, the graph
package's tests, which build their graphs with `worst_route.graph.testing`
(`docs/reference/graph.md`, **Test builders**) and check both backends against networkx and
against each other with hypothesis; `tests/engine/`, the engine's tests, with shared graph
builders in `tests/engine/graphs.py` and a `data_dir` fixture for the `plan` / `validate` CLI tests
(`docs/reference/engine.md`, **Tests**); `tests/api/`, the API's tests, with lattice builds and
helpers in `tests/api/support.py`, async tests on anyio's pytest plugin (`@pytest.mark.anyio`,
which httpx's dependency anyio provides) and the app driven in-process over `httpx`
(`docs/reference/api.md`, **Tests**); `tests/geocode/`, the geocoding proxy's tests, with
`FakeGeocoder` or a `NominatimGeocoder` on an `httpx.MockTransport` answering the jsonv2 bodies
under `tests/fixtures/geocode/` (`support.offline_clients` puts code that makes its own client on
one too, so no test builds a real HTTP client), and a fake clock for the cache, the rate limit
and the pause (`docs/reference/api.md`, **Geocoding**); `tests/presentation/`, the presentation's
tests (the places index, the comparison lines, the share image, and `tools/fetch_census.sh`
against a `curl` stand-in on PATH), whose `support.py` writes the places index from
`tests/fixtures/census/`, hand-written GeoJSON with the Census's columns: two fake states across
the API tests' lattice and four places in two per-state files, one a CDP
(`docs/reference/presentation.md`, **Tests**); `tests/golden/`, the golden routes, frozen per
build and
`graph_version` and rewritten only under a tier A brief; and `tests/fixtures/osm/`, hand-written
OSM XML that pyosmium reads directly, with no conversion (`docs/reference/pipeline.md`,
**Fixtures**). `tests/` and every test folder under it are packages (an empty `__init__.py`
each), so pytest's default import mode tolerates the same test file name in two folders and the
`tests.*` mypy override applies; a new test folder gets an `__init__.py` too.
The `data` and `slow` markers are registered in `pyproject.toml`, which runs pytest with
`--strict-markers` and a 300 s timeout per test. `slow` marks a test of a few seconds or more:
one that starts real worker processes (`tests/api/test_worker_process.py`), or the two pipeline
engines compared on random street lattices (`tests/pipeline/test_arrays_property.py`); the gate
still runs them, and `-m "not slow"` leaves them out of a quick local run.
The frontend's tests are vitest files beside its modules (`frontend/src/**/*.test.ts`), with
streams recorded from the server under `frontend/src/__fixtures__/`; they run in Node with
`npm test` and in the gate, never reach the network, and read nothing but the recordings and
`docs/reference/api.md` (`docs/reference/frontend.md`, **Tests**).

The planned shape, from `docs/PLAN.md` §8.2: `tests/fixtures/osm/*.osm` hand-written
OSM XML (a grid with one-ways, a divided highway with mismatched junctions, a cloverleaf, a
diamond, a roundabout, a cul-de-sac neighbourhood, private roads, an overpass, a ref change)
read directly by pyosmium; `tests/unit/`, `pipeline/`, `engine/`,
`api/`, `property/` (hypothesis), `golden/` (frozen routes per fixture build and
`graph_version`), `bench/` (not in CI). Tests never read `data/` and never reach the network; a
test that needs a built graph carries `@pytest.mark.data` and runs on demand with
`uv run pytest -m data` and `WORST_ROUTE_DATA` pointing at the build.

## The gate: `tools/gates.sh`

One command, run from any tree, that a change must pass before it is reported, reviewed or
landed. In order: `uv sync --frozen`; `ruff check` and `ruff format --check`; `mypy src`;
`pytest -q -m "not data"`; under `frontend/` when it exists, `npm ci` if `node_modules` is
missing, then `npm run typecheck` (tsc and the prettier check), `npm run build` and
`npm run test`; and a scan for `[verify]` instrumentation
under `src`, `tests`, `tools` and `frontend/src`. One line per gate, the failing gate's output in
full, non-zero exit on any failure. It never downloads and never reads `data/`. Before M1's
bootstrap (no `pyproject.toml`) the Python gates report `skipped` and the gate passes on the
leftover scan alone.

`.github/workflows/gate.yml` runs `tools/gates.sh` on every push and pull request
(ubuntu-latest, uv with Python 3.12, Node 22 for the frontend gate).

## Measuring a build: `tools/bench_build.py`

```
uv run python tools/bench_build.py --region oregon [--source PATH] [--steps filter,build,...]
    [--force] [--allow-fragmented] [--max-mem-gb GB] [--min-free-gb GB]
    [--out tools/out/bench_build-<region>.json]
```

Runs a region's pipeline steps, `filter` to `qa` (all seven by default, in order; never `download`),
each as `worst-route pipeline <step> --region R` in a process of its own, echoing its output, with
`--source` passed to `filter`, `--force` to every step and `--allow-fragmented` to `cleanup` and
`tiers`. Per step it records the wall time, the user and system CPU seconds, the peak memory of the
step's processes (on macOS the physical footprint, what `footprint` and Activity Monitor's Memory
column report, read with `proc_pid_rusage` as each process's lifetime peak; elsewhere the resident
set size, from `ps`), read every `WATCH_INTERVAL_S` (0.5 s) and once more when the step's output
ends, the peak resident set size of the step's process (`os.wait4`'s `ru_maxrss`, which is what
`/usr/bin/time -l` reports on macOS: it leaves out the pages macOS has compressed or swapped out, so
a step past the machine's memory reads far below its footprint, as the national build did at about
2.2–2.6 GB against about 29 GB), the exit code, the output, and the sizes of the files the step
wrote (the filtered roads and POIs, the stage pickles and reports, the build's `arrays/`,
`geometry/` (the edges' shapes in the compact format), `strings.json` and `meta.json`, the `qa/`
layers), and the free space on the data directory's volume before it. A step the CLI skips as up to
date is reported as skipped. Two guards keep a heavy run from swapping the machine or filling its
disk: `--max-mem-gb` stops a step whose memory, as read above (the physical footprint on macOS, the
resident set size elsewhere), passes the limit (SIGTERM, on which a pipeline step exits as on an
exception and removes its temporary files, `cli.exit_on_sigterm`; SIGINT would not do, since a
shell's background job starts with it ignored; then SIGKILL after `KILL_GRACE_S`, 5 s; it counts the
step's descendants but signals the step's own process, which serves while no step starts processes
of its own, and leaves the reaping to the run), the row saying "killed at X GB" with the size and
the time in the report; `--min-free-gb` starts no step while the volume has less free, the row
saying "not started" and the run exiting 3. The first step that fails, is stopped or is not started
ends the run. It prints a Markdown table, writes the JSON report to `--out` (under `tools/out/`,
which git ignores) and exits with the failed step's code, else 0. It needs real extracts under
`WORST_ROUTE_DATA`, so the gate never runs it; it is a heavy job when the region is a state
(`docs/reference/pipeline.md`, **Oregon**). `tests/pipeline/test_bench_build.py` runs it on the
divided fixture.

## Building regions in turn: `tools/build_regions.py`

```
uv run python tools/build_regions.py (--regions portland ... | --states) [--max-mem-gb 18]
    [--min-free-gb 15] [--out tools/out/build_regions.json]
```

Builds regions one after another, each the same way: those `--regions` names (`config.REGIONS`: a
city's bbox region like `portland`, a state's, a national one), or with `--states` every region of
one state, the 48 contiguous states and DC (`docs/reference/pipeline.md`, **The state builds**),
smallest extract first, so that the biggest come last. Each region is a `tools/bench_build.py` run
from `filter` to `qa`, under its watchdog (18 GB of memory by default) and its disk floor (15 GB
free), its report under `tools/out/regions/`. A `cleanup` or `tiers` that refuses a graph too
fragmented is followed by a second run with `--allow-fragmented` (cleanup reruns too, since its
parameters record the flag; the steps before it skip as up to date), whose QA then fails on what was
dropped and is recorded. Every build whose QA ran is offered to `pipeline prune`, which takes one
that passed or one cleaned with `--allow-fragmented` that failed only the connectivity rule's
thresholds (the table then says "fragmented: retained 0.966, largest 351 nodes, pruned", whichever
apply) and refuses the rest, whose stages stay; every build written gets its places index (`aux
places`). A step the watchdog stopped or a step not started for want of disk ends the batch, which
exits with that step's code; any other failure is recorded and the next region runs. A region whose
latest build, at this pipeline version, already holds a QA report is not run again, its row read
from that report and its earlier bench reports, and is offered to `prune` again, so a batch ended
early can be restarted and a rerun prunes what an earlier run left. It prints a Markdown table
(region, build, nodes, edges, road km, size, time, peak memory, QA) and rewrites its JSON report to
`--out` after each region. It needs the extracts and the Census files under `WORST_ROUTE_DATA` and
never downloads, so the gate never runs it; with `--states` it is a heavy job, run on the heavy slot
alone. `tests/pipeline/test_build_regions.py` runs it on the fixtures (a city region that passes,
one too fragmented, the disk floor, the regions or the states in their order, the table's words).

## Comparing the pipeline engines: `tools/parity.py`

`uv run python tools/parity.py --region R` runs both pipeline engines on a region's filtered
roads, each step in its own process, into a temporary directory, and reports each step's
seconds and peak memory and whether the two engines' cleanup, enrich and tiers reports and
builds are the same, array by array, the geometry's too, each step on its own engine's stages as
the CLI runs it
(`docs/reference/pipeline.md`, **The arrays engine**). It reads `WORST_ROUTE_DATA` and writes
under `tools/out/`. A state's run is a heavy job, arranged by the brief.

## Counting cut-off roads: `tools/border_ends.py`

```
uv run python tools/border_ends.py --region oregon [--compare oregon-washington] [--states PATH]
    [--out tools/out/border_ends-<region>.json]
```

Finds the dead ends of a region's filtered roads (a node that ends a way and that nothing else
references: one neighbour in the raw graph), places each in the Census 5m state polygon it lies
in (`--states`, default `<WORST_ROUTE_DATA>/aux/census/cb_2025_us_state_5m.zip`; a point on a
shared line lies in the region if either state is its own), and prints how many lie outside the
region's states, by state ("none" outside every state). Those are where a Geofabrik extract cut a
road off beyond the line (`docs/reference/pipeline.md`, **Regions of several states**). With
`--compare R2` it looks each up in R2's filtered roads: the road continues, still ends, or the
node is absent. It writes the JSON report (every such dead end with its position, state and
status) to `--out` under `tools/out/`. It reads filtered roads already on disk, so the gate does
not run it; `tests/pipeline/test_border_ends.py` runs it on the two border fixtures.

## The desktop app's tools

`tools/desktop_data.py` (pack the app's data from the builds, stage a pack for a build, write
the US outline its basemap is cut to), `tools/build_desktop.sh` (the frontend, PyInstaller and
the platform's installer, for the machine it runs on) and `tools/desktop_smoke.py` (a built app
driven headless: ready, two trips planned and validated, a clean stop) are the release
workflow's and a person's, never the gate's (`docs/reference/desktop.md`, **Building it**,
**The smoke test**). A build is a heavy job (about five minutes, 2 GB of disk); the data it packs
is read through `WORST_ROUTE_DATA` and written under the lane.

## Lanes

The main working tree is the coordinator's. A developer or reviewer works in a `git clone` under
its scratch directory: `uv sync` builds the virtualenv from the lockfile; `npm ci` under
`frontend/` when it exists. A lane reads built graphs through `WORST_ROUTE_DATA` pointing at the
main tree's `data/` (builds are immutable unless `pipeline save --force` replaces one, which
changes its `graph_version`) and writes pipeline output only under its
own `data/`. Nothing in a lane downloads. A lane's server binds a free port. Heavy jobs (a
state-scale pipeline step, a benchmark) run one at a time, arranged by the brief.

## Landing: `tools/land_series.sh`

```
tools/land_series.sh <patch-dir> <base-commit> <tree-hash> [--dry-run]
```

Run on the main tree, on `main`, clean, at the base commit. It checks every patch's message (no
trailers, no trailing period on the subject), applies the series with `git am`, compares the
resulting tree hash with the one the developer reported, runs the gate, and leaves the commits
in place, unpushed. A mismatch or a patch that does not apply resets `main` to the base.
`--dry-run` applies, checks and resets without running the gate.

## Seeing the app

The coordinator runs the API and the frontend on the main tree and looks through the browser.
Agents verify in their clones through `worst-route plan` (which prints the validator report),
`worst-route validate`, the tests, and the frontend on their own ports, driven by headless
Chrome over the DevTools protocol with the tile server blocked and screenshots kept in their
scratch directory (`docs/reference/frontend.md`, **Seeing it**). A Playwright script under
`tools/` that drives a lane's own server and writes screenshots to `tools/out/` (gitignored)
is a later item.
