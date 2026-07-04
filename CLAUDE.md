# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Rolodex is a single-file GTK4/Adwaita desktop app (`rolodex.py`, ~2500 lines) that stores
credentials in one AES-encrypted vault file. There is no build system, no test suite, no
package manifest, and this directory is not a git repo — it's a standalone script.

## Running

```bash
python3 rolodex.py
```

Runtime deps (system, not pip): GTK 4, libadwaita (`gi` / PyGObject), and the Python
`cryptography` package. No virtualenv is used — deps come from the system Python.

The `.desktop` launcher (`rolodex.desktop`) points `Exec`/`Icon` at a stale path
(`/mnt/Emulators/storage_backup_2026-05-08/...`), not this directory. If you touch install
wiring, fix that path to the real location (`/mnt/Games/Scripts/Linux/Rolodex/`).

## Architecture

The file is organised top-to-bottom as **pure logic → GUI**. The pure layer (roughly the
first 300 lines) has no GTK imports and is the safest place to make and reason about changes.

**Encryption layer** (`derive_key`, `save_vault`, `load_vault`, `create_vault`):
- On-disk format is `MAGIC(4 bytes "VLT1") + salt(16 bytes) + Fernet ciphertext`.
- Key = PBKDF2-HMAC-SHA256, 600k iterations, over the master password + per-vault salt,
  fed into Fernet. The salt is stored in the clear inside the file (standard practice).
- Vault files are always written with mode `0o600` via `os.open(...O_CREAT, 0o600)` — keep
  that when adding any new write path (backup, export both do this).

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
  owner of persistence: `self._save()` re-encrypts and writes the whole vault. Every mutation
  (add/edit/delete/import/restore/move-category/reorder) ends by calling `_save()` then
  `_refresh_list()`. Follow that pattern — there is no autosave or dirty-tracking.
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
- `Backups/` — timestamped encrypted `.vault` copies.
- `parse_text_file()` import default points at `/var/mnt/Storage/Backup/Logins.txt`, another
  stale path from an earlier machine layout.

## Conventions

- Keep the pure logic layer GTK-free so it stays trivially testable/reasoned-about.
- Master-password changes (`_finish_change_password`) rotate the salt and re-encrypt on save;
  they verify the *current* password against the in-memory `self.password`, not by re-decrypting.
- This directory sits under `/mnt/Games`, whose project `CLAUDE.md` requires
  `SUDO_ASKPASS=/usr/libexec/ssh/ksshaskpass sudo -A -p "..."` for any privileged command —
  never bare `sudo`.
