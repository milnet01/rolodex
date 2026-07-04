# File & Naming Standards

## Repository files

| Kind | Convention | Example |
|------|-----------|---------|
| Application code | lowercase, single word | `rolodex.py` |
| Documentation (root) | `UPPERCASE.md` for the well-known set (`LICENSE` is extensionless by convention) | `README.md`, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `ROADMAP.md`, `CLAUDE.md` |
| Documentation (topic) | `kebab-case.md` under `docs/` | `docs/coding-standards.md` |
| Desktop integration | reverse-DNS app id for `StartupWMClass`, plain name for the file | `rolodex.desktop`, id `com.rolodex.Contacts` |
| Icons | app name + extension | `rolodex.svg` |

## Generated / user data (never committed)

| File | Notes |
|------|-------|
| `contacts.vault` | The encrypted vault. Git-ignored. |
| `*.vault` | Any vault or backup copy. Git-ignored. |
| `.rolodex.conf` | Window geometry, plaintext JSON. Git-ignored. |
| `rolodex_export_*.txt` | Plaintext exports. Git-ignored. |
| `Backups/` | User-maintained folder for backup copies. Git-ignored; not auto-populated. |
| `contacts_backup_<YYYYMMDD>_<HHMMSS>.vault` | Timestamped backups produced by the app. |

Timestamped artifacts use `%Y%m%d_%H%M%S` so they sort chronologically by name.

## Identifiers in code

- **Functions & variables:** `snake_case`.
- **Classes:** `PascalCase`. GTK-derived widgets carry a role suffix — `<Thing>Row`,
  `<Thing>Dialog`, `<Thing>Window` (`EntryRow`, `AddEditDialog`, `MainWindow`). The suffix
  names the widget's *role*, which usually but not always matches its base: `UnlockDialog` is
  styled as a dialog yet extends `Gtk.Window`. The top-level `Adw.Application` subclass is
  `RolodexApp`.
- **Constants:** `UPPER_SNAKE_CASE` at module scope (`VAULT_FILE`, `ITERATIONS`, `MAGIC`).
- **Internal/private methods & attributes:** single leading underscore (`_save`,
  `_refresh_list`, `_current_entry_id`). GTK signal handlers are named `_on_<event>`
  (`_on_add`, `_on_row_selected`, `_on_drop`).
- **Vault schema keys:** lowercase snake_case strings. Top-level: `version`, `categories`,
  `entries`. Per-entry: `name`, `category`, `fields`, `notes`, `created`, `modified`.
  Per-field: `label`, `value`, `sensitive`. These are a serialization contract — renaming one
  is a schema change that requires a `version` bump and a `migrate_vault()` step.

## Paths

- No absolute, machine-specific paths in committed code or config. Derive locations from
  `APP_DIR` (next to the script) or `GLib.get_home_dir()`. Personal paths are how private
  info leaks into a public repo.
