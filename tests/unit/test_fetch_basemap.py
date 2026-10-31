"""`tools/fetch_basemap.sh` without the network: a stand-in for go-pmtiles on PATH (it logs its
arguments and writes its output file, or fails partway), and a local git repository standing for
protomaps/basemaps-assets. The extract kept only once complete, a failed one keeping the file
there was, the refusals before anything runs (a flag without its value among them), a dry run
writing nothing, relative paths taken as the caller's, and the assets: the four fonts, their
licence and the grayscale icons, nothing else."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "tools" / "fetch_basemap.sh"
BBOX = "-123.05,45.25,-122.35,45.72"
FONTS = [
    "Noto Sans Regular",
    "Noto Sans Medium",
    "Noto Sans Italic",
    "Noto Sans Devanagari Regular v1",
]

# Logs its arguments, one call per line (and the directory it runs in, apart); writes
# "fake <url>" as its output (its third argument) unless a dry run; with $PMTILES_FAIL set, writes
# half of that and fails.
PMTILES = """#!/bin/sh
echo "$@" >> "$PMTILES_LOG"
pwd >> "$PMTILES_LOG.cwd"
case "$*" in *--dry-run*) echo "would fetch 123 tiles"; exit 0 ;; esac
if [ -n "$PMTILES_FAIL" ]; then printf 'fa' > "$3"; exit 1; fi
printf 'fake %s' "$2" > "$3"
"""


def run(
    tmp_path: Path, *args: str, cwd: Path | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    (shim / "pmtiles").write_text(PMTILES)
    (shim / "pmtiles").chmod(0o755)
    values = {
        **os.environ,
        "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
        "WORST_ROUTE_DATA": str(tmp_path / "data"),
        "PMTILES_LOG": str(tmp_path / "pmtiles.log"),
        "PMTILES_FAIL": "",
        **env,
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=values,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "pmtiles.log"
    return log.read_text().splitlines() if log.exists() else []


def test_an_area_of_a_build_is_cut_and_kept_once_complete(tmp_path: Path) -> None:
    result = run(tmp_path, "portland", "--build", "20261030", "--bbox", BBOX, "--maxzoom", "14")
    assert result.returncode == 0, result.stderr
    basemap = tmp_path / "data" / "basemap"
    tiles = basemap / "portland.pmtiles"
    assert result.stdout == f"wrote {tiles}\n"
    url = "https://build.protomaps.com/20261030.pmtiles"
    part = basemap / ".portland.pmtiles.part"
    assert calls(tmp_path) == [f"extract {url} {part} --bbox={BBOX} --maxzoom=14"]
    assert tiles.read_text() == f"fake {url}" and not part.exists()
    # A failed extract keeps the file there was, and leaves no part.
    failed = run(tmp_path, "portland", "--build", "20261031", "--bbox", BBOX, PMTILES_FAIL="1")
    assert failed.returncode == 1 and "the extract failed" in failed.stderr
    assert tiles.read_text() == f"fake {url}" and not part.exists()


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    result = run(tmp_path, "us", "--build", "20261030", "--region", "us.geojson", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "would fetch 123 tiles\n"
    assert calls(tmp_path)[0].endswith("--region=us.geojson --dry-run")
    assert not (tmp_path / "data").exists()  # not even the basemap directory
    planet = run(tmp_path, "us", "--build", "20261030", "--planet", "--dry-run", "--assets")
    assert planet.returncode == 0, planet.stderr
    assert planet.stdout.startswith("would download https://build.protomaps.com/20261030.pmtiles")
    assert not (tmp_path / "data").exists()


def test_relative_paths_are_the_caller_s(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "area.geojson").write_text("{}")
    result = run(
        tmp_path,
        "us",
        "--build",
        "20261030",
        "--region",
        "area.geojson",
        cwd=work,
        WORST_ROUTE_DATA="rel-data",
    )
    assert result.returncode == 0, result.stderr
    assert (work / "rel-data" / "basemap" / "us.pmtiles").is_file()
    assert calls(tmp_path)[0].endswith(" --region=area.geojson")
    # pmtiles ran where the caller is, so the relative file is the caller's
    assert (tmp_path / "pmtiles.log.cwd").read_text().strip() == str(work.resolve())


def test_refusals_come_before_anything_runs(tmp_path: Path) -> None:
    for args in (
        [],
        ["Portland!", "--build", "20261030", "--bbox", BBOX],
        ["portland"],  # neither a build nor the assets
        ["portland", "--build", "latest", "--bbox", BBOX],
        ["portland", "--build", "20261030"],  # no area
        ["portland", "--build", "20261030", "--bbox", BBOX, "--planet"],
        ["portland", "--build", "20261030", "--bbox", BBOX, "--zoom", "3"],
        ["portland", "--build", "20261030", "--bbox", BBOX, "--maxzoom"],  # a flag's value missing
        ["portland", "--build"],
        ["portland", "--build", "20261030", "--bbox", "--maxzoom", "12"],
    ):
        result = run(tmp_path, *args)
        assert result.returncode == 2, (args, result.stdout, result.stderr)
        assert result.stderr.startswith(("usage:", "not a", "name the area")), args
    assert calls(tmp_path) == []
    missing = run(tmp_path, "portland", "--build", "20261030", "--bbox", BBOX, PMTILES="nope")
    assert missing.returncode == 1 and "nope not found" in missing.stderr


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def test_the_assets_are_the_fonts_their_licence_and_the_grayscale_icons(tmp_path: Path) -> None:
    repo = tmp_path / "basemaps-assets"
    for font in [*FONTS, "Noto Sans Bold"]:
        (repo / "fonts" / font).mkdir(parents=True)
        (repo / "fonts" / font / "0-255.pbf").write_bytes(font.encode())
    (repo / "fonts" / "OFL.txt").write_text("SIL Open Font License")
    sprites = repo / "sprites" / "v4"
    sprites.mkdir(parents=True)
    for flavor in ("grayscale", "light"):
        for suffix in (".json", ".png", "@2x.json", "@2x.png"):
            (sprites / f"{flavor}{suffix}").write_text(f"{flavor}{suffix}")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-qm", "assets")
    commit = git(repo, "rev-parse", "HEAD")
    result = run(
        tmp_path,
        "portland",
        "--assets",
        BASEMAP_ASSETS_REPO=str(repo),
        BASEMAP_ASSETS_COMMIT=commit,
    )
    assert result.returncode == 0, result.stderr
    basemap = tmp_path / "data" / "basemap"
    assert result.stdout.splitlines()[-1] == (
        f"wrote {basemap}/fonts and {basemap}/sprites from basemaps-assets {commit}"
    )
    assert sorted(p.name for p in (basemap / "fonts").iterdir()) == sorted([*FONTS, "OFL.txt"])
    for font in FONTS:
        assert (basemap / "fonts" / font / "0-255.pbf").read_bytes() == font.encode()
    assert sorted(p.name for p in (basemap / "sprites").iterdir()) == [
        "grayscale.json",
        "grayscale.png",
        "grayscale@2x.json",
        "grayscale@2x.png",
    ]
    assert calls(tmp_path) == []  # no build named: no tiles
