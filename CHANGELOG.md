# Changelog

All notable changes to Rolodex are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Changed
- Import file picker now opens in the user's home directory instead of a hardcoded personal path.
- `rolodex.desktop` ships with a placeholder install path and a `Security` category.
- Entry count label now singularises ("1 entry" instead of "1 entries").

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

<!-- No version tags are cut yet; these point at browsable pages. Once v1.0.0 is tagged,
     switch [Unreleased] to .../compare/v1.0.0...HEAD and [1.0.0] to .../releases/tag/v1.0.0. -->
[Unreleased]: https://github.com/milnet01/rolodex/commits/main
[1.0.0]: https://github.com/milnet01/rolodex/commits/main
