# Changelog

All notable changes to Rolodex are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/milnet01/rolodex/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/milnet01/rolodex/releases/tag/v1.1.0
<!-- 1.0.0 predates the public repo and was never tagged; link points at the history. -->
[1.0.0]: https://github.com/milnet01/rolodex/commits/main
