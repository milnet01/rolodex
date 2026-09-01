# Changelog

All notable changes to Rolodex are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Regression tests for every fix above that can be tested without a display**
  31 new tests covering the vault, config, import, clipboard, two-factor and updater fixes. Verified by mutation testing: reintroducing each defect makes the suite fail.

- **Opt-in update check that only ever installs a signed release** (ROLO-0037)
  Rolodex can now tell you when a newer version is out and install it for you.
  It **never checks on its own until you turn it on** — tick "Check for updates
  automatically" in the app menu. You can also check once at any time with
  "Check for updates...", which contacts GitHub whether or not automatic
  checking is on, because choosing it is itself the consent.

  When an update is found you are shown what changed and choose Later, Skip
  This Version, or Update Now. Nothing installs on its own.

  An update is only ever installed if it carries a valid signature from the
  Rolodex release key, checked over the exact bytes downloaded. A tampered
  download, a wrong signature, or a missing one all mean nothing is installed
  and your current version is untouched. The check sends nothing about you and
  nothing from your vault, needs no unlock, and is the app's only network
  access — see DESIGN.md and SECURITY.md.

  In-app updating applies to the downloadable builds. Running from source,
  updating is `git pull` as before.

  Note for maintainers: until `scripts/gen-signing-key.py` has been run and the
  signing key added to the repository, the built-in key is a placeholder that
  verifies nothing, so the feature offers updates it will refuse to install.
  That is deliberate — it fails closed rather than open.

### Changed

- **Build and CI hardening**
  All GitHub Actions are pinned to a specific commit rather than a moving tag, so a re-pointed tag cannot introduce new code into a release build. Checkout no longer leaves credentials in the workspace. The Linux and macOS build self-tests have the same timeout the Windows one already had, so a hang fails the build instead of blocking a runner for six hours. certifi is now named in the build scripts' prerequisites and asserted by the local CI gate, since the release binaries are built with it. A missing typelib now fails the Windows build immediately rather than producing a binary that fails mysteriously at runtime.

### Fixed

- **Several smaller correctness fixes**
  A failed update download now reports itself instead of ending silently. A vault written by a future version of Rolodex is refused rather than relabelled. "Hide" no longer re-ticks itself after you un-tick it and then edit the label. Secret fields tell the system not to keep them in input-method history or spellcheck. Generating a password while peeking no longer leaves it on screen. Short generated passwords can now contain digits and symbols. A malformed otpauth:// link no longer makes an entry unopenable. Two-factor settings outside the standard ranges are rejected. A base32 seed containing characters that look like base32 after case-folding is rejected rather than silently decoded to the wrong secret. Toast messages escape field labels, so a label containing "&" or "<" displays correctly. Release notes from GitHub are stripped of control and text-direction characters before being shown.

- **Dragging a category to the bottom of the list now works**
  A dragged category always landed just above the one you dropped it on, so the last position was unreachable and there was no other way to get there.

- **Backups are created private, and an interrupted backup cannot destroy the previous one**
  The backup was created readable by other users and only made private afterwards, leaving a window during which the whole encrypted vault was exposed. It also overwrote the destination in place, so a backup interrupted over a previous good one destroyed it.

- **Importing an empty file no longer creates a nameless entry**
  An empty or blank file produced one entry with no name instead of the "No entries found in file." message.

- **Clearing the search box brings back the entry you had selected**
  Typing until the selected entry dropped out of the results discarded the selection for good; clearing the search then showed nothing. Collapsing a category did the same.

- **A hand-edited settings file can no longer stop the app opening**
  A stray value in .rolodex.conf — which the README invites you to edit — crashed during startup and left the unlock window stuck on "Unlocking..." forever. Bad values now fall back to their defaults, the settings file is written atomically so an interrupted save cannot blank it, and any failure after a successful unlock is now shown rather than freezing the dialog.

- **A corrupt vault now says so instead of reporting a wrong password**
  A truncated or damaged vault file failed to decrypt and was reported as "Wrong password." — which, for an app with no password recovery, invites you to delete the file and start again, destroying something a backup restore could have salvaged.

- **Passwords with a leading or trailing space are stored exactly as typed**
  Saving an entry trimmed spaces off every value, silently altering any secret that deliberately had one, with no way to express it.

- **Copying works on macOS and Windows, and no longer fails on Linux when wl-clipboard is installed under X11**
  Copying only ever tried the three Linux clipboard tools, so it did nothing at all on macOS and Windows. Separately, it gave up on the first tool it found even when that tool failed — so merely having wl-clipboard installed on an X11 desktop broke every copy, while a working alternative sat untried.

- **Typing now counts as activity for the auto-lock**
  Only mouse movement reset the idle timer, so writing a long note without touching the mouse got you locked out mid-edit, losing the open dialog. The comment in the code had claimed key presses counted; now they do.

- **Cancelling a restore now actually cancels it**
  Pressing Cancel or Escape while a backup was being unlocked closed the dialog but did not stop the work, so a cancelled restore still went on to overwrite your live vault.

- **Opening Rolodex a second time no longer creates a second window that overwrites the first**
  Launching Rolodex while it was already running put a fresh unlock screen over the live window, and unlocking made a second, independent copy of your vault in memory. Whichever window saved last wiped out the other's changes with no warning. A second launch now just brings the existing window forward.

- **Changing your master password can no longer leave you locked out**
  The new password was adopted before the re-encrypted vault was known to have been written. If that write failed, the app looked like the change had not taken while actually holding the new password — and the next edit would quietly re-encrypt your vault with a password you may never have written down. The vault is now written first and the new password adopted only once it has landed. Restoring from a backup had the same flaw and is fixed the same way.

- **A failed save is now reported instead of silently pretending to work**
  If the vault could not be written — a full disk, a read-only folder — the app carried on as though it had saved. You would only find out at the next unlock, with the change gone. It now tells you.

- **An update that finishes after you lock the app no longer installs itself**
  If a download completed after you locked or closed Rolodex, it went ahead and replaced the program and restarted it — potentially while you were typing your master password into the lock screen. It now discards the download instead. Leftover part-downloaded files, which nothing previously removed, are also cleaned up at startup.

### Security

- **Locking now clears the clipboard and the on-screen entry**
  Locking the vault left a copied password on the clipboard until its timer ran out — and forever if you had turned the clipboard timer off. It also left the last-viewed entry's values in the window. Both are now cleared when you lock.

- **Two-factor seeds are now hidden like any other secret**
  A field holding a 2FA seed was shown in plain text unless its label happened to contain one of the general secret keywords — so a field named "2FA" or "TOTP" displayed the long-term secret openly, right next to the code generated from it. Recognised seeds are now always hidden, including an otpauth:// link pasted under any label at all. This applies to entries already in your vault, not only newly saved ones.

- **The release-signing key no longer sits in a job that has already run third-party code**
  The workflow that builds Rolodex is now split in two. Building happens with no signing key present and read-only permissions; a separate step downloads the finished binaries onto one clean machine, installs a fixed version of its one dependency, and only then signs them. The key is also read straight from its secret store rather than being written to a file, so it can no longer be left behind on disk when a step fails partway.

## [1.3.1] - 2026-08-27

### Fixed

- **Vault saves are now atomic — an interrupted save can no longer corrupt your vault**
  save_vault (and the plaintext export) now write to a temporary file in the
  same folder, flush it to disk, then atomically rename it into place. An
  interrupted write — a crash, a full disk, or a power cut mid-save — leaves the
  previous vault intact instead of truncating your only copy of your credentials.

## [1.3.0] - 2026-07-17

### Added

- **Generate TOTP 2FA codes from stored authenticator secrets.** (ROLO-0006)
  Store an otpauth:// URI or a 2FA-labelled base32 setup key and Rolodex renders the rotating RFC 6238 code inline with a countdown ring and one-click copy. Pure-stdlib TOTP — no new dependency.

- **Password health checkup: flag weak and reused secrets** (ROLO-0008)
  A new "Password health..." menu item opens a read-only report that scores every stored secret on length and character-class variety (Weak/Fair/Good/Strong) and flags any secret reused across entries, worst first. All analysis runs in-process over the decrypted vault — nothing leaves the app.

- **GitHub Actions CI: ruff lint + pytest on every push/PR** (ROLO-0020)
  New .github/workflows/ci.yml runs ruff and the pytest suite on push and PR to main, installing the system GTK stack from apt so `import rolodex` resolves. Pinned actions/checkout@v7.

- **Keyboard shortcuts for common actions (ROLO-0007)**
  Ctrl+F focuses search, Ctrl+N adds an entry, Ctrl+Shift+C copies
  the selected entry's password/secret (plain Ctrl+C still copies
  selected text), Ctrl+L locks the vault, Escape clears the search
  box, and Ctrl+? opens a keyboard-shortcuts reference.

- **Unsaved-changes guard (ROLO-0022) — closing the add/edit dialog with edits in flight now confirms before discarding them.**

- **Duplicate-name warning (ROLO-0023) — saving an entry whose name matches another (case-insensitive) now asks for confirmation first.**

- **Show/hide (eye) toggle on sensitive fields in the add/edit editor (ROLO-0021) — peek at a masked value while editing, view-only so it never changes whether the field is stored as a secret.**

### Changed

- **Extract shared helpers: single 0600 file-write, container-clear, dialog scaffold** (ROLO-0019)
  Internal refactor, no behaviour change. The owner-only (0600) write now lives in one write_private_file() used by both the vault save and the plaintext export; a clear_container() replaces three hand-rolled "remove all rows" loops; and make_dialog_scaffold() collapses the ToolbarView+HeaderBar+Clamp boilerplate repeated across all six dialogs. Net 53 fewer lines.

- **Debounced sidebar search (ROLO-0018) — the list rebuilds once typing pauses (~150ms) instead of on every keystroke.**

## [1.2.0] - 2026-07-16

### Added

- **Prebuilt Windows binary (`rolodex-windows-x86_64.exe`) is now available (ROLO-0031) — the GTK-bundling issue that withheld it from v1.1.0 is resolved, so all three platforms now ship self-contained single-file binaries.**

- **Built-in password generator (ROLO-0004) — a button on sensitive fields in the add/edit editor opens a popover to generate a strong random password, with length and character-class options.**
  Uses Python's `secrets` module and guarantees at least one character from each selected class.

### Changed

- **New application icon — a glossy Rolodex card-file design (`rolodex.png`), replacing the flat `rolodex.svg`.**
  Corners are transparent (rounded-square silhouette), so the icon renders
  cleanly on any desktop background. `rolodex.desktop`, the README, and the
  file-naming doc now reference the PNG.

### Security

- **Automatic clipboard clearing (ROLO-0003) — a copied secret is wiped from the clipboard a few seconds later, but only if you haven't copied something else in the meantime.**
  Delay is configurable via `clipboard_clear_seconds` in `.rolodex.conf` (default 20s; 0 disables).

- **Automatic vault lock on idle (ROLO-0002) — after a period of inactivity the vault re-locks, wiping the decrypted data and master password from memory. Adds a Lock button (Ctrl+L) for locking on demand.**
  Idle timeout is configurable via `idle_lock_seconds` in `.rolodex.conf` (default 300s; 0 disables).

## [1.1.0] - 2026-07-04

First public release with prebuilt, self-contained binaries for **Linux and macOS** (a Windows
build is in progress — see below), plus the full open-source documentation set and a round of
review-driven bug fixes.

### Added
- Public open-source release: MIT `LICENSE`, `README.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  a `docs/` standards set, a `DESIGN.md`, retroactive feature specs under `docs/specs/`, and a
  project `ROADMAP.md`.
- Dependency management standard (`docs/dependency-management-standards.md`) with a
  known-incompatible-versions ledger, plus a `requirements.txt` (`cryptography>=44.0.0`, latest
  preferred; 44.0.0 is the security floor).
- Expanded roadmap covering UI, UX, performance, refactoring, accessibility, theming,
  packaging (incl. self-contained Linux/Windows/macOS builds), and tooling
  (ROLO-0001 … ROLO-0032), not just security.
- Seed automated test suite (`tests/test_vault.py`, pytest) covering the encryption round-trip,
  wrong-password handling, `0600` permissions, migration idempotency, and the write-error
  regression. Broader coverage is tracked as ROLO-0001.
- Cross-platform packaging: a PyInstaller spec (`packaging/rolodex.spec`) and a GitHub Actions
  workflow (`.github/workflows/build.yml`) that build a single-file, self-contained binary and
  publish it to a Release on `v*` tags. Each build runs a `--selftest` gate on its native runner,
  so only binaries that actually load the GTK stack are published. Linux and macOS pass and ship;
  the Windows build currently fails its self-test (GTK bundle) and is withheld (ROLO-0031).

### Changed
- Import file picker now opens in the user's home directory instead of a hardcoded personal path.
- `rolodex.desktop` ships with a placeholder install path and a `Security` category.
- Entry count label now singularises ("1 entry" instead of "1 entries").
- Packaged (frozen) builds store the vault and config in a per-user data directory
  (`~/.local/share/Rolodex` etc.) instead of next to the executable, so data survives across
  runs. Running from source is unchanged (data stays next to `rolodex.py`).

### Fixed
- Vault save and plaintext export no longer double-close the file descriptor on a write error,
  which previously raised `OSError(EBADF)` and masked the original error. Regression-tested.
- The Add/Edit field editor no longer shows a value in cleartext while it will be saved as
  sensitive: value visibility now tracks the "Hide" checkbox.

## [1.0.0] - 2026-02-27

Initial versioned release of the app as it existed before open-sourcing. (This version
predates the public repository, so no `v1.0.0` git tag exists yet; the date reflects when the
app reached this state, not a tagged release.)

### Added
- Encrypted vault: PBKDF2-HMAC-SHA256 (600k iterations) + Fernet, `0600` file permissions.
- GTK 4 / libadwaita UI: unlock/create flow, searchable sidebar, detail pane.
- Categories with collapse/expand, drag-and-drop between categories, and management dialog.
- Sensitive-field masking with auto-detection, per-entry reveal, and colour-coded field types.
- One-click clipboard copy (`wl-copy` / `xclip` / `xsel`).
- Text-file import with preview and duplicate detection.
- Encrypted backup & restore, plaintext export, and master-password change.
- Vault schema migration (v1 → v2) applied on load.

[Unreleased]: https://github.com/milnet01/rolodex/compare/v1.3.1...HEAD
[1.3.1]: https://github.com/milnet01/rolodex/releases/tag/v1.3.1
[1.3.0]: https://github.com/milnet01/rolodex/releases/tag/v1.3.0
[1.2.0]: https://github.com/milnet01/rolodex/releases/tag/v1.2.0
[1.1.0]: https://github.com/milnet01/rolodex/releases/tag/v1.1.0
<!-- 1.0.0 predates the public repo and was never tagged; link points at the history. -->
[1.0.0]: https://github.com/milnet01/rolodex/commits/main
