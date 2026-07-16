#!/usr/bin/env bash
# Build the self-contained, single-file macOS binary and self-test it.
#
# MUST run on real macOS (Apple hardware) — this is exactly what the GitHub 'macos-latest' job
# runs (after `brew install`-ing the GTK stack + PyInstaller and pip-installing cryptography
# into the Homebrew Python, then calling this script). PyInstaller bundles Python +
# GTK4/libadwaita + `cryptography` into ONE file. The result is unsigned — first launch needs
# right-click -> Open to get past Gatekeeper.
#
# This CANNOT be built on Linux. There is no legal or practical macOS build environment on
# non-Apple hardware: Apple's EULA restricts macOS virtualization to Apple hardware, no macOS
# container images exist, and darling/osxcross cannot produce a PyInstaller GTK4 bundle. Build
# the released binary on the GitHub macos-latest runner (or a real Mac).
#
# Prereqs (Homebrew):
#   brew install gtk4 libadwaita pygobject3 py3cairo pyinstaller
#   "$(brew --prefix)/bin/python3" -m pip install --break-system-packages --upgrade cryptography
#
# Usage (on macOS): bash packaging/macos-build.sh [asset-name]
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root
ASSET="${1:-rolodex-macos-arm64}"

# Use the Homebrew Python that pygobject3 linked `gi` into; the Homebrew `pyinstaller` on PATH
# targets that same interpreter.
pyinstaller packaging/rolodex.spec --noconfirm
mkdir -p out
mv dist/rolodex "out/$ASSET"

out=$("out/$ASSET" --selftest)
echo "$out"
echo "$out" | grep -q "selftest: OK" || { echo "::error::binary failed to load GTK"; exit 1; }
echo "macOS build OK — self-contained single file: out/$ASSET"
