#!/usr/bin/env bash
# Build the self-contained, single-file Linux binary and self-test it.
#
# This is the exact build+self-test the GitHub 'ubuntu-latest' job runs (it installs the deps
# in a prior step, then calls this script). PyInstaller bundles the Python runtime, the
# GTK4/libadwaita stack + GObject-introspection typelibs, and `cryptography` into ONE file —
# the result under out/ has no runtime dependencies to install.
#
# Prereqs (CI installs these; on a dev box install once):
#   - GTK: python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 gir1.2-adw-1
#          libgtk-4-1 libadwaita-1-0   (Debian/Ubuntu names; adjust for your distro)
#   - pip: pyinstaller cryptography
#
# NOTE on portability: the binary requires a glibc at least as new as the build host's. CI
# builds on Ubuntu 24.04 (glibc 2.39) for broad compatibility — a binary built on a
# bleeding-edge distro will only run on equally-new systems. Build the *released* artifact on
# CI, not on a rolling-release workstation.
#
# Usage: bash packaging/linux-build.sh [asset-name]
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root
ASSET="${1:-rolodex-linux-x86_64}"

python3 -m PyInstaller packaging/rolodex.spec --noconfirm
mkdir -p out
mv dist/rolodex "out/$ASSET"

# Self-test: the binary imports the whole GTK/Adw/cryptography stack and exits 0 if the bundle
# is intact. Prefer a virtual display when headless (as CI is); use the real one otherwise.
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xvfb-run >/dev/null; then
    out=$(xvfb-run -a "out/$ASSET" --selftest)
else
    out=$("out/$ASSET" --selftest)
fi
echo "$out"
echo "$out" | grep -q "selftest: OK" || { echo "::error::binary failed to load GTK"; exit 1; }
echo "Linux build OK — self-contained single file: out/$ASSET"
