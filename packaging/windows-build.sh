#!/usr/bin/env bash
# Build the self-contained, single-file Windows .exe and self-test it.
#
# MUST run inside an MSYS2 UCRT64 shell on real Windows — this is exactly what the GitHub
# 'windows-latest' job runs (after installing the MSYS2 GTK stack + pip pyinstaller, then
# calling this script). PyInstaller bundles Python + GTK4/libadwaita + the GObject-introspection
# typelibs + `cryptography` into ONE .exe with no runtime deps.
#
# This CANNOT be built on Linux. The Windows GTK4 runtime comes from MSYS2, and Wine is not
# real Windows — see ROADMAP ROLO-0031 ("a Linux/Wine container does not help ... Wine != real
# Windows"). Build the released .exe on the GitHub windows-latest runner (or a real Windows box).
#
# Prereqs (MSYS2 UCRT64):
#   pacman -S  mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita \
#              mingw-w64-ucrt-x86_64-python-gobject mingw-w64-ucrt-x86_64-python-cryptography \
#              mingw-w64-ucrt-x86_64-python-pip
#   python -m pip install --upgrade pyinstaller
#
# Usage (in an MSYS2 UCRT64 shell): bash packaging/windows-build.sh [asset-name]
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root
ASSET="${1:-rolodex-windows-x86_64.exe}"

# Windows-form MSYS2 prefix so the spec can bundle the GTK typelibs (PyInstaller's automatic
# collection misses them on MSYS2 → "Namespace Gtk not available" at runtime).
export ROLODEX_MINGW="$(cygpath -w "$MINGW_PREFIX")"
echo "MINGW prefix: $ROLODEX_MINGW"

python -m PyInstaller packaging/rolodex.spec --noconfirm
mkdir -p out
mv dist/rolodex.exe "out/$ASSET"

# The shipped .exe is windowed (no console), so stdout isn't visible — gate on the EXIT CODE.
# A hard timeout turns a GTK-load hang into a build failure instead of a blocked runner.
timeout 120 "out/$ASSET" --selftest
echo "Windows build OK — self-contained single .exe: out/$ASSET"
