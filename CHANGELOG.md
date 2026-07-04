# Changelog

All notable changes to Rolodex are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Public open-source release: MIT `LICENSE`, `README.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  a `docs/` standards set, and a project `ROADMAP.md`.

### Changed
- Import file picker now opens in the user's home directory instead of a hardcoded personal path.
- `rolodex.desktop` ships with a placeholder install path and a `Security` category.

## [1.0.0] — 2026-02-27

Initial versioned release of the app as it existed before open-sourcing.

### Added
- Encrypted vault: PBKDF2-HMAC-SHA256 (600k iterations) + Fernet, `0600` file permissions.
- GTK 4 / libadwaita UI: unlock/create flow, searchable sidebar, detail pane.
- Categories with collapse/expand, drag-and-drop between categories, and management dialog.
- Sensitive-field masking with auto-detection, per-entry reveal, and colour-coded field types.
- One-click clipboard copy (`wl-copy` / `xclip` / `xsel`).
- Text-file import with preview and duplicate detection.
- Encrypted backup & restore, plaintext export, and master-password change.
- Vault schema migration (v1 → v2) applied on load.

[Unreleased]: https://github.com/milnet01/rolodex/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/milnet01/rolodex/releases/tag/v1.0.0
