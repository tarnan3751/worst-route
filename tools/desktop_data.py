"""The desktop app's data (`docs/reference/desktop.md`, **Its data**): packed once by a person,
staged by every build.

    uv run python tools/desktop_data.py pack OUT.tar.gz [--region us-t3]
        [--basemap FILE.pmtiles --basemap-name us] [--basemap-url URL]
    uv run python tools/desktop_data.py stage PACK.tar.gz DIR [--sha256 HEX] [--replace]
        [--basemap-url URL] [--contact EMAIL]
    uv run python tools/desktop_data.py conus OUT.geojson

`pack` writes, from `WORST_ROUTE_DATA`, what the app serves and nothing else: for each region (by
default `us-t3`) its latest build's `meta.json`,
`strings.json`, `arrays/` and `geometry/` (not its `qa/` or `stages/`) and its `latest` file; the
places index's states, outlines and each region's towns (format 2's files, with a `places.json` of
the regions packed); the basemap's fonts and icons
(`<data>/basemap/fonts/` and `sprites/`, from `tools/fetch_basemap.sh --assets`) when they are
there; and `desktop.json`, the app's manifest: the regions in order, their build ids, the basemap
file the app fetches on its first start (its name, size and sha256, read from `--basemap`, the
extract `tools/fetch_basemap.sh` cut, and its address `--basemap-url`, or none yet), and the
contact (none: the build writes it). It prints the pack's size and sha256, which
`packaging/desktop/data.json` records once the pack is a release asset (a person uploads it; no
agent does).

`stage` checks a pack against `--sha256` when given, unpacks it into DIR (refusing a member that
would land outside it), which must be empty or not exist (with `--replace`, what it holds is
deleted first), and writes into its manifest the basemap's address and the contact when
given (the release build's `WORST_ROUTE_BASEMAP_URL` and `WORST_ROUTE_CONTACT`), so moving the
basemap to another host is a change of variable, not of pack. `tools/build_desktop.sh` builds from
DIR (`WORST_ROUTE_DESKTOP_DATA`).

`conus` writes the contiguous US (the 48 states and DC of the national builds, from the places
index's states, buffered 10 km and simplified) as GeoJSON, the area
`tools/fetch_basemap.sh us --region OUT.geojson --maxzoom 11` cuts the desktop's basemap to. Its
output is also the frontend's `frontend/public/conus.json`, the outline the map draws the land
outside the contiguous US flat around (`docs/reference/frontend.md`, **Basemap**), committed as
the command writes it and written again with `conus frontend/public/conus.json` when the Census
outline changes.

It never downloads, and is not part of the gate.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from worst_route.config import REGIONS, settings

SERVED = ("meta.json", "strings.json", "arrays", "geometry")  # what the app reads of a build
MANIFEST = "desktop.json"
FORMAT = 1


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def manifest_of(args: argparse.Namespace, builds: dict[str, str]) -> dict[str, Any]:
    basemap = None
    if args.basemap is not None:
        basemap = {
            "name": args.basemap_name,
            "url": args.basemap_url,
            "size": args.basemap.stat().st_size,
            "sha256": sha256_of(args.basemap),
        }
    return {
        "format": FORMAT,
        "regions": list(builds),
        "builds": builds,
        "basemap": basemap,
        "contact": None,
    }


def pack(args: argparse.Namespace) -> None:
    from worst_route.presentation.places import places_dir, table_files, write_manifest

    data = settings().data_dir
    builds: dict[str, str] = {}
    members: list[tuple[Path, str]] = []
    for region in args.region or ("us-t3",):
        latest = data / "graphs" / region / "latest"
        build_id = latest.read_text().strip()
        builds[region] = build_id
        members.append((latest, f"graphs/{region}/latest"))
        for name in SERVED:
            path = data / "graphs" / region / build_id / name
            if not path.exists():
                sys.exit(f"{path} is missing: the build is not whole")
            members.append((path, f"graphs/{region}/{build_id}/{name}"))
    places, packed = places_dir(), []
    for table in ("states", "states_outline", *builds):
        files = table_files(places, table)
        if all(f.exists() for f in files):
            members += [(f, f"aux/places/{f.name}") for f in files]
            packed.append(table)
        else:
            print(f"no {files[0]}: the app goes without it", file=sys.stderr)
    for name in ("fonts", "sprites"):
        path = data / "basemap" / name
        if path.is_dir():
            members.append((path, f"basemap/{name}"))
        else:
            print(f"no {path}: the basemap is drawn without labels", file=sys.stderr)
    manifest = manifest_of(args, builds)
    with tempfile.TemporaryDirectory() as scratch:
        written = Path(scratch) / MANIFEST
        written.write_text(json.dumps(manifest, indent=1) + "\n")
        members.append((written, MANIFEST))
        index = write_manifest(Path(scratch), (r for r in builds if r in packed))
        members.append((index, f"aux/places/{index.name}"))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(args.out, "w:gz", compresslevel=6) as tar:
            for path, name in members:
                tar.add(path, arcname=name)
    print(json.dumps(manifest, indent=1))
    size = args.out.stat().st_size
    print(f"wrote {args.out}: {size / 1e6:,.1f} MB, sha256 {sha256_of(args.out)}")


def stage(args: argparse.Namespace) -> None:
    if args.sha256 is not None and sha256_of(args.pack) != args.sha256.lower():
        sys.exit(f"{args.pack} is not the pack named (its sha256 differs)")
    if args.dir.exists() and any(args.dir.iterdir()):
        if not args.replace:
            sys.exit(f"{args.dir} is not empty: stage into an empty directory, or pass --replace")
        shutil.rmtree(args.dir)
    args.dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.pack, "r:gz") as tar:
        tar.extractall(args.dir, filter="data")  # nothing outside DIR, no links out of it
    path = args.dir / MANIFEST
    manifest = json.loads(path.read_text())
    if args.basemap_url and manifest["basemap"] is not None:
        manifest["basemap"]["url"] = args.basemap_url
    if args.contact:
        manifest["contact"] = args.contact
    path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"staged {args.pack} in {args.dir}: regions {', '.join(manifest['regions'])}")


def conus(args: argparse.Namespace) -> None:
    import shapely

    from worst_route.presentation.places import places_dir, read_table

    columns, shapes = read_table(places_dir(), "states", "us-t3")
    fips = REGIONS["us-t3"].fips
    states = shapes[[f in fips for f in columns["STATEFP"]]]
    area = shapely.union_all(states).buffer(0.1).simplify(0.05)  # about 10 km out, 5 km off
    args.out.write_text(shapely.to_geojson(area) + "\n")
    bounds = tuple(round(b, 3) for b in area.bounds)
    print(f"wrote {args.out}: {len(states)} states, bounds {bounds}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("pack", help="pack the app's data from WORST_ROUTE_DATA")
    p.add_argument("out", type=Path)
    p.add_argument("--region", action="append", choices=sorted(REGIONS), help="repeatable")
    p.add_argument("--basemap", type=Path, help="the basemap file the app fetches")
    p.add_argument("--basemap-name", default="us", help="its name in the app (default us)")
    p.add_argument("--basemap-url", help="where the app fetches it from (default: none yet)")
    s = commands.add_parser("stage", help="check and unpack a pack for a build")
    s.add_argument("pack", type=Path)
    s.add_argument("dir", type=Path)
    s.add_argument("--sha256", help="the pack's, as packaging/desktop/data.json records it")
    s.add_argument("--replace", action="store_true", help="delete what DIR holds first")
    s.add_argument("--basemap-url", help="the basemap file's address, in place of the pack's")
    s.add_argument("--contact", help="the contact for the app's User-Agent")
    c = commands.add_parser("conus", help="the contiguous US as GeoJSON, for fetch_basemap.sh")
    c.add_argument("out", type=Path)
    args = parser.parse_args()
    {"pack": pack, "stage": stage, "conus": conus}[args.command](args)


if __name__ == "__main__":
    main()
