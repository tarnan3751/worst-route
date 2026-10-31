# The desktop app frozen by PyInstaller (docs/reference/desktop.md, **Building it**): one folder,
# for this machine's platform, run by tools/build_desktop.sh (`pyinstaller --distpath ...
# --workpath ... packaging/desktop/worst-route-desktop.spec`) with the data staged in
# WORST_ROUTE_DESKTOP_DATA (tools/desktop_data.py stage) and the frontend built.
#
# - The app's code and its dependencies, by PyInstaller's analysis from launch.py and the hooks
#   (hooks-contrib's for numba, llvmlite, shapely, pyproj, pyarrow and uvicorn; pywebview's own),
#   less the pipeline's packages (the app never builds a graph) and, on Linux, pywebview (the app
#   opens the browser there).
# - The Numba kernels' module also as its source file: a frozen module's code names a file that
#   does not exist, for which Numba ignores NUMBA_CACHE_DIR and caches in a directory of its own
#   choosing; with the file there, the kernels compile once into the user's directory.
# - The data (`data/`), the frontend's build (`frontend/`), and THIRD-PARTY-LICENSES.txt: the
#   licence of every distribution the app ships (those whose top-level packages the analysis
#   collected, and PyInstaller, whose bootloader the app runs) and Python's.
# - macOS: `Worst Route.app`, its executable `Worst Route`, ad-hoc signed by PyInstaller, or by
#   the identity MACOS_SIGNING_IDENTITY names (the release workflow's, when its secrets exist), with
#   the hardened runtime's exceptions in entitlements.plist; Windows and Linux: a folder
#   `worst-route-desktop`, its executable of that name, windowed on Windows, a console on Linux.
import os
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]  # SPECPATH, workpath and the classes: PyInstaller's
DATA = Path(os.environ["WORST_ROUTE_DESKTOP_DATA"])
FRONTEND = ROOT / "frontend" / "dist"
VERSION = re.search(r'__version__ = "(.+)"', (ROOT / "src/worst_route/__init__.py").read_text())[1]
MACOS, LINUX = sys.platform == "darwin", sys.platform.startswith("linux")
NAME = "worst-route-desktop"
IDENTITY = os.environ.get("MACOS_SIGNING_IDENTITY") or None

for needed in (DATA / "desktop.json", FRONTEND / "index.html"):
    if not needed.is_file():
        raise SystemExit(f"{needed} is missing: stage the data and build the frontend first")


def write_licenses(path: Path, shipped: set[str]) -> Path:
    """The name, version, licence and licence files of each distribution `shipped` names (by
    its normalised name), then Python's."""
    parts = []
    dists = [d for d in metadata.distributions() if normal(d.metadata["Name"]) in shipped]
    for dist in sorted(dists, key=lambda d: d.metadata["Name"].lower()):
        name, version = dist.metadata["Name"], dist.version
        licence = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        if len(licence) > 200 or not licence:
            classifiers = dist.metadata.get_all("Classifier") or []
            licence = "; ".join(c.split(" :: ")[-1] for c in classifiers if c.startswith("License"))
        texts = []
        for file in dist.files or []:
            stem = file.name.upper()
            if stem.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")):
                located = Path(dist.locate_file(file))
                if located.is_file():
                    texts.append(located.read_text(errors="replace"))
        parts.append(f"{'=' * 78}\n{name} {version}: {licence}\n{'=' * 78}\n" + "\n".join(texts))
    python = Path(sys.base_prefix) / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
    for candidate in (python / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE.txt"):
        if candidate.is_file():
            parts.append(f"{'=' * 78}\nPython {sys.version.split()[0]}\n{'=' * 78}\n")
            parts.append(candidate.read_text(errors="replace"))
            break
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts))
    return path


def normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def shipped_distributions(entries: list[tuple[str, str, str]]) -> set[str]:
    """The distributions whose top-level packages are among `entries` (the analysis's modules
    and binaries, by their names in the app), and PyInstaller."""
    owners = metadata.packages_distributions()
    tops = {re.split(r"[./\\]", name)[0] for name, _, _ in entries}
    return {normal(d) for top in tops for d in owners.get(top, [])} | {"pyinstaller"}


# The pipeline's packages (and the test and type tools) are never imported by the app; their
# imports inside functions (`aux places` reads Census files with geopandas) would still pull them.
EXCLUDES = ["geopandas", "osmnx", "osmium", "networkx", "pyogrio", "fiona", "IPython", "tkinter"]
EXCLUDES += ["pytest", "hypothesis", "mypy", "PyInstaller"]
# pywebview's other platforms (its guilib imports each inside a function).
PLATFORMS = {"cocoa", "winforms", "edgechromium", "win32", "gtk", "qt", "cef", "mshtml", "android"}
OWN = {"cocoa"} if MACOS else {"winforms", "edgechromium", "win32"}
EXCLUDES += ["webview"] if LINUX else [f"webview.platforms.{p}" for p in PLATFORMS - OWN]

a = Analysis(
    [str(ROOT / "packaging" / "desktop" / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(DATA), "data"), (str(FRONTEND), "frontend")],
    hiddenimports=["worst_route.api.worker"],  # what a spawned job worker unpickles
    excludes=EXCLUDES,
    module_collection_mode={"worst_route.graph.kernels": "py"},
)
LICENSES = Path(workpath) / "THIRD-PARTY-LICENSES.txt"
write_licenses(LICENSES, shipped_distributions(a.pure + a.binaries))
a.datas += [("THIRD-PARTY-LICENSES.txt", str(LICENSES), "DATA")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Worst Route" if MACOS else NAME,
    console=LINUX,
    codesign_identity=IDENTITY,
    entitlements_file=str(ROOT / "packaging" / "desktop" / "entitlements.plist") if IDENTITY else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
if MACOS:
    app = BUNDLE(
        coll,
        name="Worst Route.app",
        bundle_identifier="io.github.tarnan3751.worst-route",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Worst Route",
            "CFBundleShortVersionString": VERSION,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        },
    )
