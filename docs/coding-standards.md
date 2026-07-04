# Coding Standards

These rules describe how Rolodex's code is written today and how it should stay. They are
tuned to *this* project — a single-file GTK 4 / libadwaita Python app — not generic advice.

## Language & style

- **Python 3.10+.** Use modern idioms: `list[dict]` not `List[Dict]`, `X | Y` unions,
  `match`/`case` where it beats an `isinstance` ladder.
- Follow **PEP 8** with a soft line length of ~100 columns.
- Use **type hints** on function signatures in the pure-logic layer. GUI callbacks may omit
  them where the GTK signal signature makes them noise.
- Prefer f-strings for formatting.
- Standard-library and third-party imports at the top of the file, grouped and alphabetised
  within groups. `gi.require_version(...)` must precede the `gi.repository` import — this is
  load-bearing, not stylistic.

## Architecture boundary (the one rule that matters most)

The file is split into a **pure-logic layer** and a **GUI layer**, separated by the
`GTK4 / Adwaita GUI` banner comment.

- The pure-logic layer (encryption, data operations, category helpers, import parsing,
  clipboard, config) **must not import or reference `gi` / GTK**. It takes and returns plain
  dicts, lists, strings, and bytes.
- All GTK code lives below the banner. Keep business logic out of widget callbacks — a
  callback should gather input, call a pure function, then update the UI.

If you find yourself wanting GTK in a pure function, or vault-mutation logic inside a widget,
stop and move it across the boundary.

## Data & persistence

- The decrypted vault is a single dict with keys `version`, `categories`, `entries`. Do not
  invent parallel state that can drift from it.
- Every mutation goes through the `MainWindow` owner and ends with `self._save()`, plus a
  `self._refresh_list()` (and/or `_show_detail()`) when the visible list or detail changes.
  A few save-only paths (password change, backup) don't refresh. There is no autosave and no
  dirty-flag — persistence is explicit.
- Any code path that writes a file containing vault data must create it with mode `0600`
  (`os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)`). Never rely on the umask.
- If you change the on-disk shape, **bump `version` and extend `migrate_vault()`** rather than
  assuming new fields exist on old vaults. `migrate_vault()` must remain idempotent.

## Concurrency

- The initial **decrypt** paths (unlock, restore) run the KDF on a `threading.Thread` and
  marshal results back with `GLib.idle_add`, so the 600k-iteration derive doesn't freeze the
  UI. Preserve that for any new password-*checking* (decrypt) flow.
- Be aware of the current gap: `_save()` re-derives the key to encrypt and runs **synchronously**
  on the UI thread, so password-change and every edit briefly block the loop. Don't add new
  synchronous KDF calls on hot paths; moving saves off-thread is tracked as future work.

## Error handling

- No bare `except:` and no silent `except Exception: pass` around real logic. Prefer catching
  the specific exception you expect (`InvalidToken`, `GLib.Error`, `OSError`). A broad
  `except Exception as e:` is acceptable only as a top-level GUI handler that *surfaces* the
  error (dialog/toast) rather than swallowing it — the existing unlock/import/backup handlers
  follow this surface-and-show pattern.
- User-facing failures surface through an `Adw.AlertDialog` or a toast — never a traceback to
  stdout that the user won't see.

## Dependencies

- The dependency budget is: GTK 4, libadwaita, and `cryptography`. Adding a fourth dependency
  requires a discussion in an issue. Prefer the standard library.

## Comments

- Comment the *why*, not the *what*. The load-bearing, non-obvious things (the logic/GUI
  boundary, the `0600` requirement, the threading dance, first-match-wins classification) each
  earn a comment; ordinary widget wiring does not.
