#!/usr/bin/env bash
# Build the Rolodex Flatpak and export it as a single-file bundle (ROLO-0010).
#
#   packaging/flatpak/build.sh [OUT_DIR]
#
# Writes OUT_DIR/rolodex.flatpak (default: dist/). Needs flatpak-builder and the GNOME 51
# runtime and SDK from Flathub. A user installs the bundle with
#   flatpak install --user rolodex.flatpak
# The work tree and local repository go under OUT_DIR/flatpak-build, never into the source tree.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$(mkdir -p "${1:-$here/../../dist}" && cd "${1:-$here/../../dist}" && pwd)"
work="$out/flatpak-build"
manifest="$here/com.rolodex.Contacts.yml"

flatpak-builder --user --install-deps-from=flathub --force-clean --disable-rofiles-fuse \
  --state-dir="$work/state" --repo="$work/repo" "$work/app" "$manifest"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
  "$work/repo" "$out/rolodex.flatpak" com.rolodex.Contacts
echo "built $out/rolodex.flatpak"
