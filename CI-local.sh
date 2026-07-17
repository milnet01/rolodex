#!/usr/bin/env bash
# Local pre-push gate. Run this before every push to catch what GitHub CI would catch, without
# waiting on (or paying for) a round-trip:
#
#   1. pytest                 — the pure-logic test suite (a regression net; CI runs this too
#                               via ci.yml (ROLO-0020), so this is a local mirror of that gate).
#   2. Linux build + selftest — via packaging/linux-build.sh, the SAME script the GitHub
#                               'ubuntu-latest' job runs. A green run here means the Linux
#                               release binary will build on CI too.
#
# What this CANNOT check locally: the Windows and macOS matrix jobs. They need their native
# runners (Windows GTK comes from MSYS2; macOS builds only on Apple hardware — see ROADMAP
# ROLO-0031 and packaging/{windows,macos}-build.sh). Validate those before a release WITHOUT
# publishing by running the workflow manually on GitHub:
#
#     gh workflow run "Build binaries"
#
# The release-attach step only fires on a v* tag, so a manual run just builds + self-tests all
# three OSes. Watch it with:  gh run watch
#
# Usage: ./CI-local.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "==> [1/2] pytest (pure-logic suite)"
python3 -m pytest tests/ -q

echo
echo "==> [2/2] Linux build + self-test (mirrors the ubuntu-latest CI job)"
bash packaging/linux-build.sh

echo
echo "All local CI checks passed. Windows/macOS build on their native runners:"
echo "  gh workflow run \"Build binaries\"   # builds + self-tests all three, no publish"
