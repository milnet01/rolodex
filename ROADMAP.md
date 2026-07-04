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
