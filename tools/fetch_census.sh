#!/usr/bin/env bash
# Fetch the Census cartographic boundary files `worst-route aux places` reads
# (docs/reference/presentation.md, **Places**): the places file (1:500k) of each state named by
# its FIPS code, then the state files (1:5m for the index, 1:20m for drawing), all of the pinned
# 2025 vintage, into $WORST_ROUTE_DATA/aux/census/ (default ./data/aux/census/). Run by a person;
# never by the gate, a test or an agent:
#
#   tools/fetch_census.sh 41 53     # Oregon's and Washington's places, and the state files
#
# Each file is fetched with curl under the project's User-Agent (config.Settings.user_agent) to a
# temporary name, then checked against its pinned sha256: a match is kept, a mismatch deleted
# (exit 1). A file with no pinned sum is kept and its sum printed, to pin here. A failed fetch
# leaves nothing behind (exit 1).
set -euo pipefail

cd "$(dirname "$0")/.."

base="https://www2.census.gov/geo/tiger/GENZ2025/shp"
out="${WORST_ROUTE_DATA:-data}/aux/census"

pinned() {
  case "$1" in
    cb_2025_us_state_5m.zip) echo 8a45692bc532dbd38938a1924f445850cef2682ea67d750d7fd2f19cfe836903 ;;
    cb_2025_us_state_20m.zip) echo 9340b6d995e971b2b4230518f4fa85e6cd9e7fe6811afabc90a6a8b1191530f6 ;;
    cb_2025_41_place_500k.zip) echo 9e06eff08882940973c5d5bbed82d433b3f4a5bb91535312186c418278eedc26 ;;
    cb_2025_53_place_500k.zip) echo 24e23e4c626d87b099c45cb5cab6df90939ddc311e6b09062e16fb2436a4a7db ;;
    cb_2025_06_place_500k.zip) echo d8aa978aa353baf70dcbf3b8d4499edf2b7f71dc8dbd33f3a6a099caf9c15607 ;;
    cb_2025_16_place_500k.zip) echo 48d40376c6f55533be3cdb808cedcff4fa4d48962e9c4f865d084d53bb4d71d2 ;;
    cb_2025_32_place_500k.zip) echo 481234154b42916ab2e6b6e0c6c6a0b2e09fa1f39d797a3c43b66b5e3b887878 ;;
    *) echo "" ;;
  esac
}

sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    sha256sum "$1" | cut -d' ' -f1
  fi
}

names=()
for fips in "$@"; do
  if ! [[ "$fips" =~ ^[0-9][0-9]$ ]]; then
    echo "not a two-digit FIPS code: $fips" >&2
    exit 2
  fi
  names+=("cb_2025_${fips}_place_500k.zip")
done
names+=(cb_2025_us_state_5m.zip cb_2025_us_state_20m.zip)

agent="$(uv run --no-sync python -c 'from worst_route.config import settings; print(settings().user_agent)')"
mkdir -p "$out"
for name in "${names[@]}"; do
  part="$out/.$name.part"
  if ! curl -fsSL --user-agent "$agent" -o "$part" "$base/$name"; then
    rm -f "$part"
    echo "$name: the fetch failed; nothing kept" >&2
    exit 1
  fi
  sum="$(sha256 "$part")"
  want="$(pinned "$name")"
  if [ -z "$want" ]; then
    mv "$part" "$out/$name"
    echo "wrote $out/$name, sha256 $sum (no pinned sum: add it to tools/fetch_census.sh)"
  elif [ "$sum" = "$want" ]; then
    mv "$part" "$out/$name"
    echo "wrote $out/$name, sha256 verified"
  else
    rm -f "$part"
    echo "$name: sha256 $sum, expected $want; not kept" >&2
    exit 1
  fi
done
