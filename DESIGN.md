# Rolodex — Design

This document explains *why* Rolodex is built the way it is. For the day-to-day architecture
map aimed at code assistants see `CLAUDE.md`; for the outward security policy see `SECURITY.md`;
for feature-level behaviour contracts see `docs/specs/`.

## Goals

- **A local, offline, encrypted store for credentials.** One master password, one file, on
  your machine. No account, no cloud, no network access of any kind.
- **Trivially auditable.** The entire app is one readable Python file. A user should be able
  to skim it and understand exactly what happens to their secrets before trusting it.
- **Native on the Linux desktop.** GTK 4 + libadwaita so it looks and behaves like a modern
  GNOME app, honouring platform conventions (header bars, dialogs, toasts).
- **Hard to lose data.** Encrypted backup/restore, an explicit export, and forward-compatible
  vault migration.

## Non-goals

- **Not a team/shared secret manager.** Single user, single vault, single machine.
- **Not a browser extension or autofill agent.** Copy-to-clipboard is the integration surface.
- **Not sync.** Moving the vault between machines is a manual file copy (it's just one file).
- **Not defence against a compromised host.** See the threat model in `SECURITY.md`.
- **Not a plugin platform.** Simplicity is a feature; extensibility is explicitly out of scope.

## Architecture

The app is a single file, `rolodex.py`, split by a banner comment into two layers:

```
┌─ Pure-logic layer (no GTK) ──────────────────────────────┐
│  encryption · data ops · categories · import · clipboard │
│  config — plain dicts/bytes in, plain values out         │
└──────────────────────────────────────────────────────────┘
┌─ GUI layer (GTK 4 / libadwaita) ─────────────────────────┐
│  RolodexApp → UnlockDialog → MainWindow → dialogs        │
└──────────────────────────────────────────────────────────┘
```

**Why the split?** It keeps every security-critical decision (key derivation, file writes,
parsing) in code that has no UI entanglement — easy to reason about, easy to test in
isolation (see roadmap ROLO-0001), and impossible to accidentally couple to a widget's
lifetime. The GUI layer is "dumb": callbacks gather input, call a pure function, and refresh.

**Single-owner persistence.** `MainWindow` holds the only live copy of the decrypted vault,
salt, and password. All mutations funnel through it and end with `self._save()` (re-encrypt
and write the whole file) followed by `self._refresh_list()` (rebuild the sidebar from
scratch). There is no autosave, no dirty tracking, and no partial writes — the whole vault is
the unit of persistence. This trades write efficiency (irrelevant at this scale) for a design
with no possible desync between memory and disk.

**Off-thread key derivation.** The KDF runs 600,000 iterations, which takes long enough to
freeze the UI. Unlock, restore, and password-change therefore run the KDF on a daemon thread
and marshal the result back with `GLib.idle_add`. This is the only concurrency in the app and
it exists solely to keep the main loop responsive.

## Data model

The decrypted vault is one JSON-serialisable dict:

```
{ "version": 2,
  "categories": ["Email", "Games", ...],          # ordered; drives sidebar grouping
  "entries": {
    "<uuid4>": {
      "name": "GitHub",
      "category": "Dev",                            # "" = uncategorised
      "fields": [ {"label": "Username", "value": "...", "sensitive": false},
                  {"label": "Password", "value": "...", "sensitive": true} ],
      "notes": "...",
      "created": "<iso8601>",
      "modified": "<iso8601>"
    }
  } }
```

Design choices:

- **UUID keys, not names.** Entries are identified by a stable UUID so renaming is free and
  ordering is a pure presentation concern (`list_entries` sorts by name at render time).
- **Ordered category list separate from entries.** Categories exist independently of whether
  any entry uses them, and their order is user-controlled (drag to reorder). An entry
  referencing a deleted category is treated as uncategorised rather than erroring.
- **Two orthogonal notions of "category".** `entry["category"]` is the *user's* grouping.
  `field_category(label)` is a *cosmetic* classifier (credential/key/identity/url/date/other)
  that only picks a border colour. And `sensitive` is a third, separate axis (mask or not).
  Keeping these independent avoids surprising coupling — e.g. a field can be an "identity"
  colour and still be masked.
- **Versioned schema + idempotent migration.** `migrate_vault()` upgrades older vaults on
  load and must stay idempotent. New fields are added here with defaults, never assumed.

## On-disk format

`contacts.vault` = `VLT1` (4-byte magic) + salt (16 bytes) + Fernet token. The magic lets the
loader reject non-vault files with a clear error; the salt is stored in the clear (standard
for password-based encryption); everything after is authenticated ciphertext. See
`docs/specs/vault-format-and-crypto.md` for the full contract.

## UI design

- **Two-pane layout** (`Gtk.Paned`): a searchable, category-grouped sidebar on the left, a
  detail card on the right, following the libadwaita list/detail idiom.
- **Progressive disclosure of secrets.** Sensitive fields render as dots until the user hits
  *Reveal*, which is per-entry and resets whenever the selection changes.
- **Colour as information.** A hardcoded dark "glass" theme (`CUSTOM_CSS`) uses coloured
  left-borders to make a card scannable at a glance. This is the one area that deliberately
  departs from stock Adwaita styling; making it theme-aware is roadmap ROLO-0011.
- **Non-destructive by default.** Every destructive action (delete entry/category, restore,
  export plaintext) is behind an `Adw.AlertDialog` confirmation.

## Key trade-offs

| Decision | We chose | We gave up |
|----------|----------|------------|
| App structure | One file, minimal deps | Modularity, plugin surface |
| Persistence | Rewrite whole vault per change | Write efficiency (a non-issue at this scale) |
| KDF | PBKDF2 600k (stdlib via `cryptography`) | Argon2's memory-hardness (see ROLO-0005) |
| Theme | Bespoke dark CSS | System light/dark following (see ROLO-0011) |
| Recovery | None — password is the only key | Convenience; in exchange, zero server-side attack surface |

## Related documents

- `CLAUDE.md` — architecture orientation for AI assistants.
- `SECURITY.md` — threat model and cryptographic design.
- `docs/security-standards.md` — engineering rules for security-relevant code.
- `docs/specs/` — per-feature behaviour specs.
- `ROADMAP.md` — proposed future work.
