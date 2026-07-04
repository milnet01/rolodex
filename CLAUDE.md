# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Rolodex is a single-file GTK4/Adwaita desktop app (`rolodex.py`, ~2480 lines) that stores
credentials in one AES-encrypted vault file. There is no build system and no test suite; the
only manifest is `requirements.txt` (the single pip dependency, `cryptography`). The project
is a git repository, published publicly at `github.com/milnet01/rolodex`.

## Running

```bash
python3 rolodex.py
```

Runtime deps: GTK 4 and libadwaita (`gi` / PyGObject) come from the system (not on PyPI);
`cryptography` is the one pip-installable dependency (`requirements.txt`), though a distro
package works too. No virtualenv is used — deps come from the system Python.

The `.desktop` launcher (`rolodex.desktop`) ships with `/path/to/rolodex` placeholders for
`Exec`/`Icon`; the user substitutes their own clone path (see the README's "Desktop launcher"
section). Don't hardcode a machine-specific absolute path into it.

## Architecture

The file is organised top-to-bottom as **pure logic → GUI**. The pure layer (roughly the
first 300 lines) has no GTK imports and is the safest place to make and reason about changes.

**Encryption layer** (`derive_key`, `save_vault`, `load_vault`, `create_vault`) — canonical
contract: `docs/specs/vault-format-and-crypto.md`:
- On-disk format is `MAGIC(4 bytes "VLT1") + salt(16 bytes) + Fernet ciphertext`.
- Key = PBKDF2-HMAC-SHA256, 600k iterations, over the master password + per-vault salt,
  fed into Fernet. The salt is stored in the clear inside the file (standard practice).
- Secret files are written owner-only (`0o600`): `save_vault` and the plaintext export use
  `os.open(..., O_CREAT, 0o600)`; the backup path copies the vault then `os.chmod(..., 0o600)`.
  Keep any new secret-writing path `0o600`.

**Data model** — the decrypted vault is one dict:
```
{ "version": 2,
  "categories": ["Games", "Email", ...],      # ordered list, drives sidebar grouping
  "entries": { "<uuid>": { "name", "category", "fields": [...], "notes",
                           "created", "modified" } } }
```
Each field is `{"label", "value", "sensitive": bool}`. `sensitive` fields are masked in the
UI and auto-detected from the label via `SENSITIVE_KEYWORDS`. Separately, `field_category()`
classifies a label into one of `credential/key/identity/url/date/other` purely for the
colored left-border in the detail view (`FIELD_CATEGORIES`, first-match-wins) — that is
cosmetic and unrelated to the `sensitive` flag or the user-defined `categories`.

**Migration** — `migrate_vault()` upgrades older vaults in place (adds `categories`, backfills
`entry["category"]`, stamps `version: 2`). It is idempotent and MUST be called after every
`load_vault` (unlock and restore both call it). If you change the on-disk shape, bump the
version and extend this function rather than assuming fields exist.

**GUI layer** (GTK4 + libadwaita):
- `RolodexApp` (`Adw.Application`) → `UnlockDialog` (create-or-unlock) → `MainWindow`.
- `MainWindow` holds the live `self.vault`/`self.salt`/`self.password` and is the single
  owner of persistence: `self._save()` re-encrypts and writes the whole vault. Mutations end by
  calling `_save()`, plus `_refresh_list()` when the sidebar changes (a few save-only paths, e.g.
  password change and backup, skip the refresh). Follow that pattern — no autosave or dirty-tracking.
- `_refresh_list()` rebuilds the sidebar from scratch on every change and has three modes:
  flat search results, category-grouped (with collapsible `CategoryHeaderRow`s), or a plain
  flat list when no categories exist.
- Decryption (unlock, restore) runs on a background `threading.Thread`, marshalling results
  back with `GLib.idle_add` so the 600k-iteration KDF doesn't freeze the UI. Preserve that
  when adding any password-checking flow.
- Drag-and-drop is used in two places: entries → category headers (sidebar), and reordering
  fields/categories inside dialogs (`Gtk.DragSource`/`Gtk.DropTarget` with typed content).

**Styling** — all visual design lives in one `CUSTOM_CSS` string near the bottom, loaded once
in `do_startup`. It's a hardcoded dark "glass" theme; the field-category border colors there
correspond to `FIELD_CATEGORIES` keys (`.field-credential`, `.field-key`, etc.).

## Sibling files

- `contacts.vault` — the user's real encrypted vault. **Never** read, move, or overwrite it
  without explicit instruction; it's live user data.
- `.rolodex.conf` — plaintext JSON window geometry only (no secrets).
- `Backups/` — a user-maintained folder for backup copies (git-ignored). The app never writes
  here automatically; its Backup action just defaults the save-dialog filename to
  `contacts_backup_<timestamp>.vault` at a location the user picks.
- The import file picker opens in the user's home directory (`GLib.get_home_dir()`); there is
  no hardcoded import path.

## Conventions

- Keep the pure logic layer GTK-free so it stays trivially testable/reasoned-about.
- Master-password changes (`_finish_change_password`) rotate the salt and re-encrypt on save;
  they verify the *current* password against the in-memory `self.password`, not by re-decrypting.
- This directory sits under `/mnt/Games`, whose project `CLAUDE.md` requires
  `SUDO_ASKPASS=/usr/libexec/ssh/ksshaskpass sudo -A -p "..."` for any privileged command —
  never bare `sudo`.
