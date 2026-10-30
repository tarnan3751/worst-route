#!/usr/bin/env bash
# The gate: everything a change must pass before it is reported, reviewed or landed
# (CLAUDE.md, **Agent workflow**). Runs, in order:
#   1. sync        uv sync --frozen            (the virtualenv from the lockfile; fails if the lock is stale)
#   2. lint        uv run ruff check . && uv run ruff format --check .
#   3. types       uv run mypy src
#   4. tests       uv run pytest -q -m "not data"   (tests that need a built graph carry the `data` marker)
#   5. frontend    npm ci (if node_modules is missing), npm run typecheck, npm run build   (when frontend/ exists)
#   6. leftovers   grep for "[verify]" under src, tests, tools and frontend/src must find nothing
# Prints one line per gate, the failing gate's output in full, and exits non-zero when any
# gate fails. The gate never downloads anything and never reads data/; it runs in whatever
# tree it is started from (a lane's clone or the main tree), so set WORST_ROUTE_DATA only
# for the `data`-marked tests you run yourself.
# Before M1's bootstrap lands (no pyproject.toml), the Python gates report "skipped" and the
# gate passes on the leftover scan alone.
set -uo pipefail

cd "$(dirname "$0")/.."

scratch="$(mktemp -d "${TMPDIR:-/tmp}/worst-route-gates.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT
failed=0

# report <name> <status> <detail> [<output-file>]
report() {
  local name="$1" status="$2" detail="$3" output="${4:-}"
  printf 'gate %-9s %s%s\n' "$name" "$status" "${detail:+ ($detail)}"
  if [ "$status" = "FAILED" ]; then
    failed=1
    if [ -n "$output" ] && [ -s "$output" ]; then
      sed 's/^/    /' "$output"
    fi
  fi
}

if [ -f pyproject.toml ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Install uv (https://docs.astral.sh/uv/) and retry." >&2
    exit 2
  fi

  # 1. Sync ---------------------------------------------------------------------------
  if [ -f uv.lock ]; then
    if uv sync --frozen >"$scratch/sync.txt" 2>&1; then
      report sync ok "frozen lockfile"
    else
      report sync FAILED "uv sync --frozen" "$scratch/sync.txt"
    fi
  else
    report sync FAILED "no uv.lock; run uv lock and commit it"
  fi

  # 2. Lint ---------------------------------------------------------------------------
  if uv run --no-sync ruff check . >"$scratch/lint.txt" 2>&1 \
      && uv run --no-sync ruff format --check . >>"$scratch/lint.txt" 2>&1; then
    report lint ok ""
  else
    report lint FAILED "ruff" "$scratch/lint.txt"
  fi

  # 3. Types --------------------------------------------------------------------------
  if [ -d src ]; then
    if uv run --no-sync mypy src >"$scratch/types.txt" 2>&1; then
      report types ok "$(tail -1 "$scratch/types.txt")"
    else
      report types FAILED "mypy" "$scratch/types.txt"
    fi
  else
    report types skipped "no src/"
  fi

  # 4. Tests --------------------------------------------------------------------------
  if [ -d tests ]; then
    uv run --no-sync pytest -q -m "not data" >"$scratch/tests.txt" 2>&1
    tests_status=$?
    summary="$(grep -E '^(=+ )?[0-9]+ (passed|failed|error)' "$scratch/tests.txt" | tail -1 | tr -d '=')"
    if [ $tests_status -eq 0 ]; then
      report tests ok "${summary:-passed}"
    else
      report tests FAILED "${summary:-exit $tests_status}" "$scratch/tests.txt"
    fi
  else
    report tests skipped "no tests/"
  fi
else
  report sync skipped "no pyproject.toml yet"
  report lint skipped "no pyproject.toml yet"
  report types skipped "no pyproject.toml yet"
  report tests skipped "no pyproject.toml yet"
fi

# 5. Frontend -------------------------------------------------------------------------
if [ -f frontend/package.json ]; then
  if ! command -v npm >/dev/null 2>&1; then
    report frontend FAILED "npm not found"
  else
    (
      cd frontend || exit 1
      if [ ! -d node_modules ]; then npm ci >"$scratch/npm-ci.txt" 2>&1 || { cat "$scratch/npm-ci.txt"; exit 1; }; fi
      npm run --silent typecheck && npm run --silent build
    ) >"$scratch/frontend.txt" 2>&1
    if [ $? -eq 0 ]; then
      report frontend ok "typecheck + build"
    else
      report frontend FAILED "" "$scratch/frontend.txt"
    fi
  fi
else
  report frontend skipped "no frontend/ yet"
fi

# 6. Leftovers ------------------------------------------------------------------------
dirs=()
for d in src tests tools frontend/src; do [ -d "$d" ] && dirs+=("$d"); done
if [ "${#dirs[@]}" -gt 0 ] && grep -rn --exclude='gates.sh' '\[verify\]' "${dirs[@]}" >"$scratch/verify.txt" 2>/dev/null; then
  report leftovers FAILED "[verify] instrumentation left in the tree" "$scratch/verify.txt"
else
  report leftovers ok ""
fi

if [ $failed -eq 0 ]; then
  echo "gates: all passed"
else
  echo "gates: FAILED"
fi
exit $failed
