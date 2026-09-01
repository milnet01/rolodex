#!/usr/bin/env bash
# Local pre-push gate. Run this before every push to catch what GitHub CI would catch, without
# waiting on (or paying for) a round-trip:
#
#   1. ruff                   — the lint gate, invoked exactly as ci.yml's Lint step does
#                               (`ruff check rolodex.py tests/`). The scope matters: a bare
#                               `ruff check .` also sweeps build/ dist/ out/ build_pyi/ and
#                               reports findings CI never sees. The rule set is declared in
#                               ruff.toml so an unpinned ruff cannot drift the gate (ROLO-0038).
#   2. pytest                 — the pure-logic test suite (a regression net; CI runs this too
#                               via ci.yml (ROLO-0020), so this is a local mirror of that gate).
#   3. Linux build + selftest — via packaging/linux-build.sh, the SAME script the GitHub
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
# The sign-and-release job only runs on a v* tag, so a manual run FROM A BRANCH just builds +
# self-tests all three OSes. Note workflow_dispatch also accepts a TAG as its ref, and
# github.ref is then refs/tags/v... -- a manual run launched against a tag does sign and
# publish. Watch it with:  gh run watch
#
# Usage: ./CI-local.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "==> [1/3] ruff (lint) — same invocation as ci.yml's Lint step"
python3 -m ruff check rolodex.py tests/

echo
echo "==> [2/3] pytest (pure-logic suite)"
python3 -m pytest tests/ -q

echo
echo "==> [3/3] Linux build + self-test (mirrors the ubuntu-latest CI job)"
# certifi is a build-time dependency of the frozen binary's TLS trust (ROLO-0037 D7) and CI
# installs it in every job. Without this assertion a green local run meant nothing about the
# shipped binary's trust store, which is precisely the drift this script exists to prevent.
python3 -c 'import certifi' 2>/dev/null || {
    echo "certifi is not installed, but CI builds with it (ROLO-0037 D7)." >&2
    echo "Install it first:  python3 -m pip install --upgrade certifi" >&2
    exit 1
}
bash packaging/linux-build.sh

echo
echo "All local CI checks passed. Windows/macOS build on their native runners:"
echo "  gh workflow run \"Build binaries\"   # builds + self-tests all three, no publish"
