#!/usr/bin/env bash
# Fetch the vector basemap the API serves under WORST_ROUTE_BASEMAP=pmtiles
# (docs/reference/hosting.md, **The basemap**; docs/reference/api.md, **Basemap**): an area of one
# of Protomaps' daily builds, cut with the go-pmtiles CLI, as $WORST_ROUTE_DATA/basemap/NAME.pmtiles
# (default the repository's data/basemap/), NAME being the basemap's name (serve --basemap-name, by
# default the first region the API serves); and with --assets the fonts and icons of its labels,
# from protomaps/basemaps-assets at a pinned commit, into fonts/ and sprites/ beside it, with
# their licences. Run by a person; never by the gate, a
# test or an agent:
#
#   tools/fetch_basemap.sh portland --build 20261030 --bbox -123.05,45.25,-122.35,45.72 --assets
#   tools/fetch_basemap.sh us --build 20261030 --region us.geojson --maxzoom 14
#   tools/fetch_basemap.sh portland --build 20261030 --bbox ... --dry-run   # the size, no download
#   tools/fetch_basemap.sh portland --assets                                # the labels alone
#
# A build is YYYYMMDD; Protomaps keeps about a week of them (https://maps.protomaps.com/builds).
# The latest:
#   curl -s https://build-metadata.protomaps.dev/builds.json \
#     | python3 -c 'import json,sys; print(max(b["key"] for b in json.load(sys.stdin)))'
# --bbox W,S,E,N or --region (a GeoJSON polygon) picks the area, --maxzoom the deepest zoom kept
# (the build's 15 by default; MapLibre draws deeper zooms from it); --planet downloads the whole
# build instead, about 139 GB. The extract reads the build over HTTP range requests. It is written
# to a temporary name and renamed once complete, so a failed run keeps the file there was. A
# relative --region FILE or WORST_ROUTE_DATA is the caller's: the script never changes directory.
# --dry-run writes nothing, not even the basemap directory.
#
# Needs `pmtiles` (go-pmtiles: https://github.com/protomaps/go-pmtiles/releases, or
# `brew install pmtiles`; $PMTILES names another) to cut an area, curl for --planet, git for
# --assets. The fonts are under the SIL Open Font License (fonts/OFL.txt, copied too). The icons
# derive from Mapzen's tangrams/icons, under the MIT License, whose notice the sprites directory
# of basemaps-assets does not carry: --assets copies it from tangrams/icons at a pinned commit, as
# sprites/LICENSE-mapzen-icons.txt.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
builds="${PROTOMAPS_BUILDS:-https://build.protomaps.com}"
assets_repo="${BASEMAP_ASSETS_REPO:-https://github.com/protomaps/basemaps-assets.git}"
assets_commit="${BASEMAP_ASSETS_COMMIT:-028c18f713baecad011301ff7a69acc39bcc2ae7}"
icons_repo="${MAPZEN_ICONS_REPO:-https://github.com/tangrams/icons.git}"
icons_commit="${MAPZEN_ICONS_COMMIT:-92510779634f4a006c61ea70e50cb8c52c765a81}"
out="${WORST_ROUTE_DATA:-$root/data}/basemap"
# The fonts the labels ask for (frontend/src/map/basemap.test.ts holds the style to them) and the
# icons of the flavor the frontend draws (api/basemap_api.py's SPRITE).
fonts=("Noto Sans Regular" "Noto Sans Medium" "Noto Sans Italic" "Noto Sans Devanagari Regular v1")
flavor="grayscale"

usage() {
  echo "usage: tools/fetch_basemap.sh NAME [--build YYYYMMDD (--bbox W,S,E,N | --region FILE | --planet)" \
    "[--maxzoom Z] [--dry-run]] [--assets]" >&2
  exit 2
}

[ $# -ge 1 ] || usage
name="$1"
shift
if ! [[ "$name" =~ ^[a-z0-9_-]+$ ]]; then
  echo "not a region name: $name" >&2
  exit 2
fi
build="" area="" planet=0 maxzoom="" assets=0 dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --build | --bbox | --region | --maxzoom)
      # a value, not the end of the line nor the next option
      if [ $# -lt 2 ] || [ -z "$2" ] || [[ "$2" == --* ]]; then
        usage
      fi
      case "$1" in
        --build) build="$2" ;;
        --bbox) area="--bbox=$2" ;;
        --region) area="--region=$2" ;;
        --maxzoom) maxzoom="$2" ;;
      esac
      shift 2
      ;;
    --planet) planet=1; shift ;;
    --dry-run) dry=1; shift ;;
    --assets) assets=1; shift ;;
    *) usage ;;
  esac
done
if [ -z "$build" ] && [ $assets -eq 0 ]; then
  usage
fi
if [ -n "$build" ]; then
  if ! [[ "$build" =~ ^[0-9]{8}$ ]]; then
    echo "not a build (YYYYMMDD): $build" >&2
    exit 2
  fi
  if { [ $planet -eq 1 ] && [ -n "$area" ]; } || { [ $planet -eq 0 ] && [ -z "$area" ]; }; then
    echo "name the area: --bbox W,S,E,N, --region FILE, or --planet" >&2
    exit 2
  fi
fi

if [ $dry -eq 0 ]; then
  mkdir -p "$out"
fi

if [ -n "$build" ]; then
  url="$builds/$build.pmtiles"
  target="$out/$name.pmtiles"
  part="$out/.$name.pmtiles.part"
  if [ $planet -eq 1 ]; then
    if [ $dry -eq 1 ]; then
      echo "would download $url, the whole planet, as $target"
    else
      curl -fL -C - -o "$part" "$url"
      mv "$part" "$target"
      echo "wrote $target"
    fi
  else
    pmtiles="${PMTILES:-pmtiles}"
    if ! command -v "$pmtiles" >/dev/null 2>&1; then
      echo "$pmtiles not found: install go-pmtiles (https://github.com/protomaps/go-pmtiles/releases)" >&2
      exit 1
    fi
    options=("$area")
    if [ -n "$maxzoom" ]; then
      options+=("--maxzoom=$maxzoom")
    fi
    if [ $dry -eq 1 ]; then
      # an output path outside the data directory, in case pmtiles opens it
      scratch="$(mktemp -d "${TMPDIR:-/tmp}/basemap-dry.XXXXXX")"
      trap 'rm -rf "$scratch"' EXIT
      "$pmtiles" extract "$url" "$scratch/$name.pmtiles" "${options[@]}" --dry-run
    elif "$pmtiles" extract "$url" "$part" "${options[@]}"; then
      mv "$part" "$target"
      echo "wrote $target"
    else
      rm -f "$part"
      echo "the extract failed; $target left as it was" >&2
      exit 1
    fi
  fi
fi

# checkout DIR REPO COMMIT PATH...: the paths of REPO at COMMIT into DIR, a sparse checkout of one
# commit (git's hashes check what arrives).
checkout() {
  local dir="$1" repo="$2" commit="$3"
  shift 3
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$repo"
  git -C "$dir" sparse-checkout set --no-cone "$@"
  git -C "$dir" fetch -q --depth 1 --filter=blob:none origin "$commit"
  git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
}

if [ $assets -eq 1 ] && [ $dry -eq 1 ]; then
  echo "would fetch the fonts and icons from basemaps-assets $assets_commit into $out"
  echo "would write the icons' licence from tangrams/icons $icons_commit" \
    "as $out/sprites/LICENSE-mapzen-icons.txt"
elif [ $assets -eq 1 ]; then
  work="$(mktemp -d "${TMPDIR:-/tmp}/basemap-assets.XXXXXX")"
  trap 'rm -rf "$work"' EXIT
  paths=("/fonts/OFL.txt" "/sprites/v4/$flavor.*" "/sprites/v4/$flavor@2x.*")
  for font in "${fonts[@]}"; do
    paths+=("/fonts/$font/")
  done
  checkout "$work/assets" "$assets_repo" "$assets_commit" "${paths[@]}"
  checkout "$work/icons" "$icons_repo" "$icons_commit" "/LICENSE.md"
  mkdir -p "$out/fonts" "$out/sprites"
  for font in "${fonts[@]}"; do
    rm -rf "${out:?}/fonts/$font"
    cp -R "$work/assets/fonts/$font" "$out/fonts/$font"
  done
  cp "$work/assets/fonts/OFL.txt" "$out/fonts/OFL.txt"
  cp "$work/assets/sprites/v4/$flavor".* "$work/assets/sprites/v4/$flavor@2x".* "$out/sprites/"
  cp "$work/icons/LICENSE.md" "$out/sprites/LICENSE-mapzen-icons.txt"
  echo "wrote $out/fonts and $out/sprites from basemaps-assets $assets_commit"
  echo "wrote $out/sprites/LICENSE-mapzen-icons.txt from tangrams/icons $icons_commit"
fi
