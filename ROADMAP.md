# Rolodex Roadmap

Planned and proposed work for Rolodex, grouped by priority. Each item is written to be
spec-ready: a future session can pick one up, write a spec under `docs/specs/`, and implement.

Status legend: 📋 planned · 🚧 in-progress · ✅ shipped · 💭 considered

## High priority

- 📋 [ROLO-0001] **Add an automated test suite for the pure-logic layer.**
  Why: there are zero automated tests today, so every change is verified by hand and refactors are risky.
  Scope: pytest over the GTK-free functions — derive_key/save_vault/load_vault round-trip, migrate_vault idempotency, parse_text_file, search_entries, category helpers. No GUI harness needed because the logic layer is already GTK-free.
  **Layman:** Safety net that checks the encryption and data code still works after any change.
  Kind: test.
  Source: in-session-2026-07-04.
  Seeded (2026-07-04): tests/test_vault.py added with round-trip, wrong-password, 0600, migrate-idempotency, and the save-vault write-error regression. Remaining: parse_text_file, search_entries, category helpers, and CI wiring (ROLO-0020).

- 📋 [ROLO-0002] **Auto-lock the vault on idle and add a manual Lock button.**
  Why: once unlocked, the vault and master password stay in memory indefinitely — a real gap if the user walks away. This is the biggest security improvement available.
  Scope: a configurable idle timeout that returns to UnlockDialog and clears the decrypted vault + password from memory, plus a toolbar Lock action. Interacts with the session lifetime in MainWindow.
  **Layman:** Re-locks the app after a period of inactivity so an unlocked vault can't sit open.
  Kind: security.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0003] **Clear the clipboard automatically a few seconds after a copy.**
  Why: copied secrets currently sit in the system clipboard until overwritten, readable by any app.
  Scope: after copy_to_clipboard, schedule a GLib timeout that clears the clipboard if its contents are unchanged. Make the delay configurable; show a countdown in the toast.
  **Layman:** Wipes a copied password from the clipboard shortly after, so it doesn't linger.
  Kind: security.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0004] **Built-in password generator in the add/edit field editor.**
  Why: a credential manager should help create strong secrets, not just store them.
  Scope: a generator control on sensitive FieldRows with length and character-class options, using the secrets module. Pure-logic function generate_password() below the boundary; small popover UI above it.
  **Layman:** A button that fills a field with a strong random password.
  Kind: feature.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0018] **Replace the full-sidebar rebuild with a model-backed list and debounced search.**
  Why: _refresh_list() tears down and recreates every sidebar row on every mutation AND on every search keystroke (search_entries scans all entries each time). Fine for a small vault, visibly wasteful for a large one.
  Scope: move to a Gtk.ListView/GtkSelectionModel backed by a data model with incremental updates, and debounce the search-changed handler (e.g. 150ms) so typing doesn't recompute per character. Keep search_entries pure and covered by ROLO-0001 tests. Biggest efficiency win in the app.
  **Layman:** Make the list update smoothly instead of rebuilding the whole thing on every keystroke.
  Kind: perf.
  Source: in-session-2026-07-04.

## Medium priority

- 📋 [ROLO-0005] **Offer Argon2id key derivation with a transparent vault migration.**
  Why: Argon2id is memory-hard and resists GPU/ASIC cracking better than PBKDF2.
  Scope: add an argon2 KDF path, record the algorithm + parameters in the vault header, bump the format version, and re-wrap the vault on next save. migrate_vault gains a KDF-upgrade branch. Requires the argon2-cffi dependency — weigh against the one-file/minimal-deps goal.
  **Layman:** Upgrade the password-scrambling to a newer, tougher method, converting old vaults automatically.
  Kind: security.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0006] **Generate TOTP 2FA codes from stored authenticator secrets.**
  Why: 'authenticator' is already a recognised sensitive keyword; users store 2FA seeds but must go elsewhere to use them.
  Scope: detect otpauth:// or base32 seeds in a field, render a live 6-digit code with a countdown ring, one-click copy. Pure-logic TOTP (RFC 6238) using hmac/hashlib — no new dependency.
  **Layman:** Show the rotating 6-digit login codes right next to the account they belong to.
  Kind: feature.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0007] **Keyboard shortcuts for the common actions.**
  Why: a keyboard-driven tool is faster and expected on the Linux desktop.
  Scope: wire Gtk.Application accelerators — Ctrl+F focus search, Ctrl+N add, Ctrl+L lock (pairs with ROLO-0002), Ctrl+C copy focused field, Escape to clear search. Add a shortcuts window (Ctrl+?).
  **Layman:** Hotkeys like Ctrl+F to search, Ctrl+N for a new entry, Ctrl+L to lock.
  Kind: enhancement.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0008] **Password health audit: flag weak, reused, and duplicate secrets.**
  Why: storing passwords is only half the value; surfacing bad ones is the other half.
  Scope: a report view scoring sensitive fields on length/variety and detecting reuse across entries. All analysis in the pure-logic layer over the decrypted vault; never leaves the process.
  **Layman:** A checkup screen that points out weak or repeated passwords across your entries.
  Kind: feature.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0009] **Filter the sidebar by category and improve search matching.**
  Why: with many entries the flat search and full grouped view are the only options today.
  Scope: a category filter control above the list, and optional fuzzy/substring-token matching in search_entries. Keep search_entries pure and covered by the ROLO-0001 tests.
  **Layman:** A quick way to show just one category, plus more forgiving search.
  Kind: enhancement.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0015] **User-selectable themes and accent colours.**
  Why: the UI is currently a single hardcoded dark 'glass' theme in CUSTOM_CSS; users want choice.
  Scope: refactor CUSTOM_CSS into named, swappable theme definitions (e.g. dark-glass, light, high-contrast, plus an accent-colour picker), a theme setting persisted in .rolodex.conf, and a Preferences UI to choose one. The field-category border colours must remain distinguishable in every theme. Builds on and supersedes ROLO-0011 (follow-system light/dark), which can become the 'Auto' option.
  **Layman:** Let people pick from several looks (colour schemes) instead of the one fixed dark theme.
  Kind: ux.
  Source: user-request-2026-07-04.

- 📋 [ROLO-0016] **Colourblind-friendly field cues that don't rely on colour alone.**
  Why: field types (credential/key/identity/url/date/other) are distinguished only by a coloured left-border today — invisible to many colourblind users, and colour-alone fails WCAG 1.4.1.
  Scope: add a redundant non-colour cue per field category — a small type icon and/or a short text tag next to the label — so the category is legible in greyscale. Verify the theme palettes (ROLO-0015) against common colourblindness simulations. Touches _show_detail and the CSS.
  **Layman:** Make the field types tell-apart-able without needing to see colour.
  Kind: accessibility.
  Source: user-request-2026-07-04.

- 📋 [ROLO-0017] **Screen-reader support: accessible names, roles, and relationships.**
  Why: icon-only buttons (add, copy, rename, delete, drag handles) and masked fields need explicit accessible names/descriptions; masked values must not be announced as raw dots, and reveal state should be conveyed.
  Scope: set Gtk.Accessible names/descriptions and appropriate roles across the UI, ensure focus order and keyboard operability (pairs with ROLO-0007 shortcuts), and test end-to-end with Orca. Announce toasts and dialog headings.
  **Layman:** Make the app work properly with screen readers that read the interface aloud.
  Kind: accessibility.
  Source: user-request-2026-07-04.

- 📋 [ROLO-0019] **Extract shared dialog scaffolding and a single 0600 file-write helper.**
  Why: every dialog rebuilds the same ToolbarView + HeaderBar + Clamp boilerplate, the sidebar/field/category lists each hand-roll the same 'remove all rows' loop, and the secure 0600 os.open pattern is duplicated in save_vault and the export path. Duplication invites drift — and the file-write duplication is a security-consistency risk.
  Scope: a small make_dialog_scaffold() helper, a clear_listbox() helper, and one write_private_file() helper used by every secret-writing path. Pure refactor, no behaviour change; lean on ROLO-0001 tests to prove it.
  **Layman:** Tidy up repeated code so the app is easier to maintain and less error-prone.
  Kind: refactor.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0020] **Add GitHub Actions CI: lint (ruff) plus the test suite.**
  Why: there is no CI; nothing currently guards a PR. Public repo = free Linux runner minutes.
  Scope: a workflow running ruff (style/lint) and the ROLO-0001 pytest suite on push/PR, on the current stable Python. Pin actions to current major versions per dependency standards. Depends on ROLO-0001 for the test half; the lint half can land immediately.
  **Layman:** Automatic checks on every change so mistakes get caught before merge.
  Kind: chore.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0021] **Show-password (eye) toggle on sensitive fields in the editor.**
  Why: sensitive field values are hidden while editing with only the 'Hide' checkbox to flip visibility of the whole row; users expect a per-field reveal eye.
  Scope: add a peek toggle to sensitive FieldRow value entries (Gtk.Entry secondary icon) that flips visibility without changing the saved sensitive flag. Small, self-contained UI change.
  **Layman:** An eye icon to peek at what you're typing into a password field.
  Kind: ux.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0022] **Warn before discarding unsaved changes in the add/edit dialog.**
  Why: Cancel/close on AddEditDialog discards everything with no confirmation — easy to lose work.
  Scope: track a dirty flag on the editor; on cancel/close with changes, show an Adw.AlertDialog to confirm discard. Applies to add and edit.
  **Layman:** Ask 'are you sure?' if you close the editor with unsaved edits.
  Kind: ux.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0023] **Warn when a new entry duplicates an existing entry name.**
  Why: the importer detects duplicate names, but manually adding a duplicate is silent — inconsistent and confusing.
  Scope: on save in AddEditDialog for a new entry, if the name (case-insensitive) already exists, prompt to confirm/rename. Reuse the same case-insensitive comparison used by import_entries.
  **Layman:** Flag it when you add an entry with the same name as one you already have.
  Kind: ux.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0024] **Adaptive layout for narrow windows using libadwaita breakpoints.**
  Why: the fixed two-pane Gtk.Paned doesn't collapse; on a narrow window the sidebar and detail fight for space.
  Scope: migrate to Adw.NavigationSplitView with an Adw.Breakpoint so the sidebar and detail become a single navigable stack below a width threshold. Presentational restructure of MainWindow.
  **Layman:** Make the app usable when the window is small or on a phone-sized screen.
  Kind: enhancement.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0025] **Multi-select entries for bulk delete and bulk move-to-category.**
  Why: every operation is one-entry-at-a-time; tidying a large vault is tedious.
  Scope: a selection mode in the sidebar (checkboxes / Ctrl-click) with a bulk action bar for delete (single confirm) and move-to-category. Interacts with _refresh_list selection handling — best sequenced after ROLO-0018.
  **Layman:** Select several entries at once to delete or re-file them together.
  Kind: feature.
  Source: in-session-2026-07-04.

- 🚧 [ROLO-0030] **Self-contained Linux build (single AppImage, no system dependencies).**
  Why: today Linux users must install GTK4, libadwaita, PyGObject and cryptography from their distro; the user wants a zero-dependency single file.
  Scope: bundle the Python runtime + GTK4/libadwaita + cryptography into one relocatable executable — AppImage (packaging the GNOME platform runtime) or PyInstaller/Nuitka one-file. Ship it as a release asset. The hard part is bundling the GTK stack and its typelib/GObject-introspection data, not the Python. Supersedes part of ROLO-0010 (Flatpak) as the dependency-free distribution path; keep Flatpak for software-center listing.
  **Layman:** A single Linux file you double-click to run — no installing Python or GTK first.
  Kind: package.
  Source: user-request-2026-07-04.
  Linux self-contained binary built and smoke-tested locally via PyInstaller (packaging/rolodex.spec, 72MB single file, GTK4/libadwaita bundled, launches; frozen build persists data to ~/.local/share/Rolodex). CI workflow (.github/workflows/build.yml) builds it on ubuntu-latest. Ships on the first v* tag.

- 🚧 [ROLO-0031] **Self-contained Windows build (single .exe, no dependencies to install).**
  Why: the user wants a Windows version that needs no separate downloads.
  Scope: produce a bundled Windows executable via PyInstaller/Nuitka with the GTK4 + libadwaita runtime from MSYS2/gvsbuild and the cryptography wheel packed in. Major effort: GTK4/libadwaita on Windows is not turnkey (theme, DLLs, GI typelibs, icon themes must all be bundled), and libadwaita's Windows support lags. Investigate feasibility first; if bundling GTK proves impractical, this is the item where a more portable UI toolkit would be evaluated (a large architectural decision, flagged not decided).
  **Layman:** A single Windows .exe that just runs, with everything bundled inside.
  Kind: package.
  Source: user-request-2026-07-04.
  CI job added (.github/workflows/build.yml, windows-latest via MSYS2 UCRT64: gtk4 + libadwaita + python-gobject + cryptography, PyInstaller). Best-effort/untested from the Linux dev box; needs CI-run iteration to confirm the GTK bundle launches on Windows.

- 🚧 [ROLO-0032] **Self-contained macOS build (single .app bundle, no dependencies to install).**
  Why: the user wants a macOS version that needs no separate downloads.
  Scope: produce a bundled .app (py2app / Briefcase / PyInstaller) with the GTK4 + libadwaita runtime (Homebrew/jhbuild) and cryptography embedded; code-sign and notarize for Gatekeeper. Same major caveat as the Windows build: GTK4/libadwaita on macOS is non-trivial to bundle and does not feel native. Investigate feasibility; shares the portable-toolkit question raised in ROLO-0031.
  **Layman:** A single macOS app you drag to Applications; everything is inside it.
  Kind: package.
  Source: user-request-2026-07-04.
  CI job added (.github/workflows/build.yml, macos-latest via Homebrew gtk4/libadwaita/pygobject3, PyInstaller, unsigned per user). Best-effort/untested; needs CI-run iteration. Unsigned .app requires right-click->Open past Gatekeeper (no Apple Developer account).

## Low priority / nice-to-have

- 📋 [ROLO-0010] **Package Rolodex as a Flatpak.**
  Why: today users must hand-install GTK4, libadwaita, and cryptography and edit the .desktop file.
  Scope: a Flatpak manifest (GNOME runtime) bundling the cryptography wheel, a proper desktop file and icon install, and filesystem access scoped to where the vault lives.
  **Layman:** A one-click install that bundles the app and its dependencies for any Linux distro.
  Kind: package.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0011] **Follow the system light/dark theme instead of a hardcoded dark theme.**
  Why: CUSTOM_CSS is a fixed dark 'glass' theme that ignores the user's preference and can look wrong in light mode.
  Scope: split the CSS into theme-aware variables via Adw.StyleManager color-scheme, or gate the dark overrides on the active scheme. Purely presentational — no data or logic change.
  **Layman:** Let the app match your desktop's light or dark setting automatically.
  Kind: ux.
  Source: in-session-2026-07-04.
  Folded into ROLO-0015 (user-selectable themes) as the 'Auto' / follow-system option; implement there rather than standalone.

- 📋 [ROLO-0012] **CSV import and export for interoperability with other managers.**
  Why: the current importer only understands one bespoke text layout; CSV eases migration from other tools.
  Scope: a CSV parser/writer alongside parse_text_file, reusing the ImportPreviewDialog. Warn loudly that CSV export is plaintext (same gating as the existing export).
  **Layman:** Move data in and out using the spreadsheet format other password apps use.
  Kind: feature.
  Source: in-session-2026-07-04.

- 💭 [ROLO-0013] **Undo for entry and category deletion.**
  Why: deletion is immediate and permanent; the confirm dialog is the only guard.
  Scope: keep the deleted record in memory and offer Undo via the existing toast overlay for a few seconds before the save is finalised.
  **Layman:** A brief 'Undo' after deleting so an accidental delete is recoverable.
  Kind: enhancement.
  Source: in-session-2026-07-04.

- 💭 [ROLO-0014] **Pin or favourite frequently used entries to the top.**
  Why: quality-of-life for users with large vaults.
  Scope: a per-entry 'pinned' flag (schema addition — version bump + migrate_vault) and a pinned group rendered first in _refresh_list.
  **Layman:** Keep your most-used logins pinned at the top of the list.
  Kind: feature.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0026] **Remember and restore the last-selected entry across sessions.**
  Why: the app already persists window geometry to .rolodex.conf; restoring the last selection is a cheap continuity win.
  Scope: store the last-selected entry id (and optionally scroll position / collapsed-category state) in .rolodex.conf and reselect on launch. Config-only; no vault change.
  **Layman:** Reopen the app where you left off, on the same entry.
  Kind: ux.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0027] **Ship AppStream metainfo so the app appears properly in software centers.**
  Why: a com.rolodex.Contacts.metainfo.xml is needed for GNOME Software / KDE Discover listings and pairs with the Flatpak (ROLO-0010).
  Scope: author the AppStream metainfo XML with summary, description, categories, and screenshots, and mirror release notes from CHANGELOG.md into its <releases> block (per documentation standards).
  **Layman:** Make the app show up nicely (name, screenshots, description) in Linux app stores.
  Kind: package.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0028] **Externalize UI strings for translation (gettext/i18n).**
  Why: all UI text is hardcoded English; internationalization widens reach and is expected of a desktop app.
  Scope: wrap user-facing strings in gettext _(), add a translation template (.pot) and a build step, and document the workflow. Touches every UI string — do it as one deliberate pass.
  **Layman:** Prepare the app so it can be translated into other languages.
  Kind: accessibility.
  Source: in-session-2026-07-04.

- 📋 [ROLO-0029] **Provide a documented sample import file and format reference.**
  Why: the text-import format (blank-line-separated blocks, 'Label: value' lines) is only described in the import spec; new users have nothing to copy.
  Scope: add examples/sample-import.txt plus a short format section in the README, kept in sync with parse_text_file (spec: docs/specs/import-export-backup.md).
  **Layman:** A ready-made example file showing exactly how to format data for import.
  Kind: doc.
  Source: in-session-2026-07-04.
