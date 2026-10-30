"""`tools/fetch_census.sh` against a stand-in for curl on PATH (no network): the URLs and the
User-Agent it asks with, a file without a pinned sum kept with its sum printed, a file whose sum
is not the pinned one deleted (exit 1), a failed fetch leaving nothing (exit 1), and a FIPS code
that is not one refused before any fetch."""

import hashlib
import os
import subprocess
from pathlib import Path

from worst_route.config import settings

SCRIPT = Path(__file__).parents[2] / "tools" / "fetch_census.sh"
BASE = "https://www2.census.gov/geo/tiger/GENZ2025/shp"

# Logs its arguments, one call per line, and writes "fake <url>" where -o says; for a URL
# containing $CURL_FAIL, writes half of that and fails as curl -f does on an HTTP error.
CURL = """#!/bin/sh
echo "$@" >> "$CURL_LOG"
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    --user-agent) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
if [ -n "$CURL_FAIL" ] && [ "${url#*$CURL_FAIL}" != "$url" ]; then
  printf 'fa' > "$out"
  exit 22
fi
printf 'fake %s' "$url" > "$out"
"""


def fetch(tmp_path: Path, *fips: str, fail: str = "") -> subprocess.CompletedProcess[str]:
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "curl").write_text(CURL)
    (shim / "curl").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
        "WORST_ROUTE_DATA": str(tmp_path / "data"),
        "CURL_LOG": str(tmp_path / "curl.log"),
        "CURL_FAIL": fail,
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *fips], env=env, capture_output=True, text=True, timeout=60
    )


def test_unpinned_files_are_kept_and_a_wrong_sum_is_not(tmp_path: Path) -> None:
    result = fetch(tmp_path, "06")
    census = tmp_path / "data" / "aux" / "census"
    places = census / "cb_2025_06_place_500k.zip"
    sum_06 = hashlib.sha256(f"fake {BASE}/{places.name}".encode()).hexdigest()
    assert result.stdout == (
        f"wrote {places}, sha256 {sum_06} (no pinned sum: add it to tools/fetch_census.sh)\n"
    )
    assert result.returncode == 1
    sum_5m = hashlib.sha256(f"fake {BASE}/cb_2025_us_state_5m.zip".encode()).hexdigest()
    assert result.stderr == (
        f"cb_2025_us_state_5m.zip: sha256 {sum_5m}, expected "
        "8a45692bc532dbd38938a1924f445850cef2682ea67d750d7fd2f19cfe836903; not kept\n"
    )
    assert sorted(p.name for p in census.iterdir()) == [places.name]  # no .part left
    calls = (tmp_path / "curl.log").read_text().splitlines()
    agent = settings().user_agent
    assert calls == [
        f"-fsSL --user-agent {agent} -o {census}/.{name}.part {BASE}/{name}"
        for name in ("cb_2025_06_place_500k.zip", "cb_2025_us_state_5m.zip")
    ]


def test_a_failed_fetch_leaves_nothing(tmp_path: Path) -> None:
    result = fetch(tmp_path, "41", fail="cb_2025_41_place_500k.zip")
    assert result.returncode == 1
    assert result.stderr == "cb_2025_41_place_500k.zip: the fetch failed; nothing kept\n"
    assert list((tmp_path / "data" / "aux" / "census").iterdir()) == []  # no .part left
    assert len((tmp_path / "curl.log").read_text().splitlines()) == 1  # and no further fetch


def test_a_bad_fips_code_fetches_nothing(tmp_path: Path) -> None:
    result = fetch(tmp_path, "41", "6")
    assert result.returncode == 2
    assert result.stderr == "not a two-digit FIPS code: 6\n"
    assert not (tmp_path / "curl.log").exists()
