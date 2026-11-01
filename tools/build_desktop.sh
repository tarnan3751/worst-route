#!/usr/bin/env bash
# Build the desktop app for this machine's platform (docs/reference/desktop.md, **Building it**):
#
#   tools/build_desktop.sh DATA_DIR [OUT_DIR]
#
# DATA_DIR is the app's data, staged by `tools/desktop_data.py stage`; OUT_DIR, by default
# tools/out/desktop/ (which git ignores), receives the work, the frozen app and the package:
#   1. the frontend: npm ci when node_modules is missing or older than its lockfile, npm run build;
#   2. PyInstaller with packaging/desktop/worst-route-desktop.spec, in the environment of
#      `uv sync --no-dev --group desktop` (run first, by hand or by the release workflow);
#   3. the package, named worst-route-<version>-<platform>: on macOS a DMG holding
#      Worst Route.app and a link to /Applications (`hdiutil`, compressed with LZMA), on Windows the
#      Inno Setup installer (packaging/desktop/worst-route.iss, `iscc`), on Linux a tar.gz of the
#      app's folder.
# Signing, when the owner's certificates exist (the release workflow's secrets): on macOS the spec
# signs with MACOS_SIGNING_IDENTITY; on Windows WINDOWS_CERTIFICATE (a .pfx) and
# WINDOWS_CERTIFICATE_PASSWORD sign the executable and the installer (`signtool`, timestamped).
# Without them the app is unsigned (ad hoc on macOS). It prints the frozen app's and the package's
# sizes, and the package's path last. It never downloads anything but the frontend's npm
# packages, and is not part of the gate.
set -euo pipefail

cd "$(dirname "$0")/.."
# Absolute paths, in Windows' own form under Git Bash (`pwd -W`), for PyInstaller and iscc.
absolute() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }
data="$(absolute "${1:?usage: tools/build_desktop.sh DATA_DIR [OUT_DIR]}")"
out="${2:-tools/out/desktop}"
mkdir -p "$out"
out="$(absolute "$out")"
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/worst_route/__init__.py)"
case "$(uname -s)" in
  Darwin) platform="macos-$(uname -m)" ;;
  Linux) platform="linux-$(uname -m)" ;;
  MINGW* | MSYS* | CYGWIN*) platform="windows-x64" ;;
  *) echo "unknown platform $(uname -s)" >&2; exit 2 ;;
esac
name="worst-route-$version-$platform"

(
  cd frontend
  if [ ! -d node_modules ] || [ package-lock.json -nt node_modules ]; then npm ci; fi
  npm run --silent build
)

rm -rf "$out/dist" "$out/work"
WORST_ROUTE_DESKTOP_DATA="$data" uv run --no-sync pyinstaller --noconfirm --log-level WARN \
  --distpath "$out/dist" --workpath "$out/work" packaging/desktop/worst-route-desktop.spec

case "$platform" in
  macos-*)
    app="$out/dist/Worst Route.app"
    staging="$out/dmg"
    rm -rf "$staging" "$out/$name.dmg"
    mkdir -p "$staging"
    cp -R "$app" "$staging/"
    ln -s /Applications "$staging/Applications"
    hdiutil create -quiet -volname "Worst Route" -srcfolder "$staging" -format ULMO \
      "$out/$name.dmg"
    rm -rf "$staging"
    package="$out/$name.dmg"
    built="$app"
    ;;
  windows-*)
    sign() {
      if [ -n "${WINDOWS_CERTIFICATE:-}" ]; then
        MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
          signtool sign /f "$WINDOWS_CERTIFICATE" /p "$WINDOWS_CERTIFICATE_PASSWORD" /fd SHA256 \
          /tr http://timestamp.digicert.com /td SHA256 "$1"
      fi
    }
    sign "$out/dist/worst-route-desktop/worst-route-desktop.exe"
    iscc="${ISCC:-C:/Program Files (x86)/Inno Setup 6/ISCC.exe}"
    # Git Bash rewrites arguments that start with a slash (/Q, /DVersion=...) into Windows paths
    # before a native program sees them, which ISCC then takes for more script files; the two
    # variables switch that off for this call (and signtool's above).
    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
      "$iscc" /Q "/DVersion=$version" "/DSource=$out/dist/worst-route-desktop" "/DOutput=$out" \
      "/DName=$name" packaging/desktop/worst-route.iss
    sign "$out/$name.exe"
    package="$out/$name.exe"
    built="$out/dist/worst-route-desktop"
    ;;
  linux-*)
    tar -czf "$out/$name.tar.gz" -C "$out/dist" worst-route-desktop
    package="$out/$name.tar.gz"
    built="$out/dist/worst-route-desktop"
    ;;
esac

echo "app: $(du -sh "$built" | cut -f1) ($built)"
echo "package: $(du -sh "$package" | cut -f1)"
echo "$package"
