# CLAUDE.md

Genre: record
Status: active

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Rolodex is a single-file GTK4/Adwaita desktop app (`rolodex.py`) that stores
credentials in one AES-encrypted vault file. There is no build system; the only manifest is
`requirements.txt` (the single pip dependency, `cryptography`). Tests are a seed suite in
`tests/` (pytest — run `pytest tests/`); broader coverage is ROLO-0001. The project is a git
repository, published publicly at `github.com/milnet01/rolodex`.

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

The file is organised top-to-bottom as **pure logic → GUI**. The pure layer — everything above
the `# GTK4 / Adwaita GUI` banner — has no GTK imports and is the safest place to make and
reason about changes. (Grep for the banner rather than trusting a line number.)

**Encryption layer** (`derive_key`, `save_vault`, `load_vault`, `create_vault`, plus their
`*_with_key` siblings) — canonical contract: `docs/specs/vault-format-and-crypto.md`:
- On-disk format is `MAGIC(4 bytes "VLT1") + salt(16 bytes) + Fernet ciphertext`.
- Key = PBKDF2-HMAC-SHA256, 600k iterations, over the master password + per-vault salt,
  fed into Fernet. The salt is stored in the clear inside the file (standard practice).
- Secret files are written owner-only (`0o600`) through **one helper**: `write_private_file()`,
  which `mkstemp`s at 0600, `fsync`s and `os.replace`s. `save_vault`, the plaintext export and
  the backup path all route through it. Keep any new secret-writing path on that helper — a
  plain `open(path, "w")` respects the umask, and `shutil.copy2` + `chmod` leaves the file
  world-readable for the length of the copy.

**Data model** — the decrypted vault is one dict:
```
{ "version": 2,
  "categories": ["Games", "Email", ...],      # ordered list, drives sidebar grouping
  "entries": { "<uuid>": { "name", "category", "fields": [...], "notes",
                           "created", "modified" } } }
```
Each field is `{"label", "value", "sensitive": bool}`. `sensitive` fields are masked in the
UI and auto-detected from the label via `SENSITIVE_KEYWORDS`. Separately, `field_category()`
classifies a label into one of `credential/key/identity/url/date/other`, which picks both the
colored left-border and the type icon at the head of the row in the detail view
(`FIELD_CATEGORIES`, first-match-wins; `FIELD_CATEGORY_CUES` holds each category's icon and
the name a screen reader speaks) — that is presentational and unrelated to the `sensitive`
flag or the user-defined `categories`.

**Migration** — `migrate_vault()` upgrades older vaults in place (adds `categories`, backfills
`entry["category"]`, stamps `version: 2`). It is idempotent and MUST be called after every
successful load. Not after `load_vault` — nothing calls that; the app calls
`load_vault_with_key` / `create_vault_with_key`, and unlock, restore and create each migrate
before the data is used. If you change the on-disk shape, bump the
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
- TOTP live codes (ROLO-0006): `parse_totp_field()` (pure layer) decides which fields get a
  code; `_show_detail` injects a "Code" row per match and runs one shared 1-second
  `GLib.timeout` (`_totp_tick`) that refreshes every visible code + countdown ring. The timer
  is cancelled in `_cancel_totp_tick` on every rebuild and on close/lock — keep that lifecycle
  intact if you touch the detail pane, or the timer leaks across entries.

**Styling** — all visual design lives in one `CUSTOM_CSS` string near the bottom. It names
colours (`@rolo_*`) and never states them: each theme in `PALETTES` (pure layer) supplies them,
and `theme_css()` joins the palette, `CUSTOM_CSS` and the palette's `extra_css`, in that order.
`ThemeManager` owns the one CSS provider and reloads it when the theme, the accent or the
desktop's light/dark changes (ROLO-0015). A new colour goes into every palette, not into
`CUSTOM_CSS`; `tests/test_themes.py` fails on a name one palette forgets, on field colours
that merge under a colourblindness simulation, and on text below 4.5:1. The field-category
border classes (`.field-credential`, `.field-key`, etc.) correspond to `FIELD_CATEGORIES` keys.

## Sibling files

- `contacts.vault` — the user's real encrypted vault. **Never** read, move, or overwrite it
  without explicit instruction; it's live user data.
- `.rolodex.conf` — plaintext JSON: window geometry plus non-secret preferences
  (`idle_lock_seconds`, `clipboard_clear_seconds`, `theme`, `accent`). No secrets.
- `Backups/` — a user-maintained folder for backup copies (git-ignored). The app never writes
  here automatically; its Backup action just defaults the save-dialog filename to
  `contacts_backup_<timestamp>.vault` at a location the user picks.
- The import file picker opens in the user's home directory (`GLib.get_home_dir()`); there is
  no hardcoded import path.

## Conventions

- Keep the pure logic layer GTK-free so it stays trivially testable/reasoned-about.
- **To exercise the GUI headlessly, start `Xvfb` yourself — `xvfb-run` is not installed here.**
  `Xvfb :99 -screen 0 1280x1024x24 &` then
  `env -u WAYLAND_DISPLAY GDK_BACKEND=x11 DISPLAY=:99 python3 …`. Setting `DISPLAY` alone is
  not enough on a Wayland desktop: GTK prefers the session's Wayland socket, so the "headless"
  windows open on the user's real screen, and a stopped Xvfb goes unnoticed. Do NOT reach for
  `GDK_BACKEND=broadway`: `Gtk.init_check()` returns **True** under it and window construction
  then raises `RuntimeError: Gtk couldn't be initialized`, so the probe says the display works
  and every later step fails for a reason that looks like a product defect. A whole
  verify-delivery run was thrown away to that once. With a real `DISPLAY`, `MainWindow` can be
  constructed directly against a throwaway vault and its handlers driven in-process, which is
  how the end-to-end feature checks were done — `contacts.vault` is never involved.
- **The live TOTP code renders grouped: `"543 878"`, not `"543878"`.** A check asserting six
  contiguous digits fails against a working feature. Strip spaces before comparing.
- **Never derive the key on a save path.** `MainWindow._key` holds the key derived at unlock,
  and `_save()` writes through `save_vault_with_key`. Calling `save_vault` there instead still
  works and still passes every round-trip test — it just silently puts 600k PBKDF2 rounds back
  on the GTK main thread, measured at ~81 ms per mutation (ROLO-0043). Re-derive only where the
  salt rotates: `_finish_change_password` and `_finish_restore`. A cached key and its salt are a
  pair; writing one with the other's salt yields a vault no password opens.
- Master-password changes (`_finish_change_password`) rotate the salt and re-encrypt on save;
  they verify the *current* password against the in-memory `self.password`, not by re-decrypting.
- **`__version__` in `rolodex.py` is version-bearing.** The updater compares against it, so
  `.claude/bump.json` rewrites it *and* its `post_check` asserts it matches the topmost dated
  CHANGELOG heading. Both halves are needed: `post_check` is a fixed shell string, so adding a
  `files[]` entry alone does not extend it, and a `__version__` that failed to rewrite would
  pass the bump silently.
- **`urllib.request` is imported lazily, inside the update fetch helpers only.** Never at
  module scope — `tests/test_update.py` asserts `import rolodex` leaves it out of
  `sys.modules`. The module-scope `import urllib.parse` near the top is a *different* module,
  is required for TOTP `otpauth://` parsing, and must stay. Scan for `urllib.request` and
  never for bare `urllib`: the wider scan fails on that correct pre-existing import, and the
  cheapest way to make it pass is deleting it, which breaks TOTP. `urllib`, `socket` and `ssl`
  are all in `sys.modules` after import anyway, via that import and via GTK and cryptography.
- **Auto-update asset matching is EQUALITY, never a prefix or suffix.** A release's required
  `<asset>.sig` is a prefix match of the asset name, so a `startswith` predicate finds two
  matches on every well-formed release, the ambiguity guard fires, and no update is ever
  offered — while a synthetic duplicate-asset test still passes. `PLATFORM_ASSETS` is keyed on
  `(sys.platform, platform.machine())`, not on platform alone: an Intel Mac must not be offered
  the arm64 binary. Windows is deliberately absent (deferred) — `os.replace` cannot swap a
  locked `.exe` and the relaunch needs `/bin/sh`.
- **The release-signing public key in `rolodex.py` is real as of ROLO-0041 (2026-09-21), and
  is not a routine thing to change.** Every binary already shipped trusts this key and nothing
  else, so replacing it makes those installs refuse every later update, and losing the private
  half does the same with no recovery. The private half lives only as the GitHub Actions secret
  `ROLODEX_SIGNING_KEY` and in the maintainer's backup — never commit it, and generate a pair
  only with `scripts/gen-signing-key.py`.
  `test_INV11_shipped_key_is_a_real_key_and_not_the_placeholder` pins the constant against a
  revert to the old 32-zero placeholder, which would silently put the updater back to failing
  closed: able to offer an update and never install one.
- **Lint scope is `ruff check rolodex.py tests/`, never `ruff check .`** — a bare `.` also
  sweeps `build/`, `dist/`, `out/` and `build_pyi/` and reports findings CI never sees.
  The rule set is declared in `ruff.toml` (`E4, E7, E9, F`) precisely because `ci.yml`
  installs ruff unpinned, so ruff's changing defaults would otherwise drift the gate under
  a codebase that hasn't changed. Widening that set is ROLO-0038, not a drive-by.
- **The pre-push gate needs two `git config` keys per clone, and says nothing if unset.**
  `git config ants.gate.command ./CI-local.sh` and
  `git config ants.gate.docsGlob '*.md:docs/**:LICENSE:.github/ISSUE_TEMPLATE/**'`.
  `core.hooksPath` is a third, set to `.githooks` so the delegating hooks run.
  All three are local git config, so they do not survive a fresh clone and an unset gate is
  indistinguishable from a passing one — the hook warns, then pushes anyway. `CI-local.sh`
  mirrors `ci.yml` step for step (ruff → pytest → Linux build); keep them in lockstep or the
  local run returns green for a pipeline that will fail.
- This directory sits under `/mnt/Games`, whose project `CLAUDE.md` requires
  `SUDO_ASKPASS=/usr/libexec/ssh/ksshaskpass sudo -A -p "..."` for any privileged command —
  never bare `sudo`.

---

Why these rules say what they say: [`docs/history/claude-md.md`](docs/history/claude-md.md).
