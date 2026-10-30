# Testing and tools

How a change is checked, where agents work, and how a series lands. The conventions are stated
in `CLAUDE.md`, **Agent workflow**; this file is the how-it-works.

## Tests

What exists: `tests/conftest.py`, which documents the `data` marker; `tests/unit/`, with the
config and CLI tests (typer's `CliRunner`); `tests/pipeline/`, with the pipeline step tests and a
`conftest.py` whose autouse fixture fails any test that would reach the network and whose
`data_dir` fixture points `WORST_ROUTE_DATA` at a temporary directory; `tests/graph/`, the graph
package's tests, which build their graphs with `worst_route.graph.testing`
(`docs/reference/graph.md`, **Test builders**) and check both backends against networkx and
against each other with hypothesis; `tests/engine/`, the engine's tests, with shared graph
builders in `tests/engine/graphs.py` and a `data_dir` fixture for the `plan` / `validate` CLI tests
(`docs/reference/engine.md`, **Tests**); `tests/golden/`, the golden routes, frozen per build and
`graph_version` and rewritten only under a tier A brief; and `tests/fixtures/osm/`, hand-written
OSM XML that pyosmium reads directly, with no conversion (`docs/reference/pipeline.md`,
**Fixtures**). `tests/` and every test folder under it are packages (an empty `__init__.py`
each), so pytest's default import mode tolerates the same test file name in two folders and the
`tests.*` mypy override applies; a new test folder gets an `__init__.py` too.
The `data` marker is registered in `pyproject.toml`, which runs pytest with `--strict-markers`
and a 300 s timeout per test.

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
missing, then `npm run typecheck` and `npm run build`; and a scan for `[verify]` instrumentation
under `src`, `tests`, `tools` and `frontend/src`. One line per gate, the failing gate's output in
full, non-zero exit on any failure. It never downloads and never reads `data/`. Before M1's
bootstrap (no `pyproject.toml`) the Python gates report `skipped` and the gate passes on the
leftover scan alone.

`.github/workflows/gate.yml` runs `tools/gates.sh` on every push and pull request
(ubuntu-latest, uv with Python 3.12, Node 22 for the frontend gate).

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
`worst-route validate`, the tests, and, once M2 lands, a Playwright script under `tools/` that
drives a lane's own server and writes screenshots to `tools/out/` (gitignored).
