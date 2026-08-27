# Rolodex — Design

This document explains *why* Rolodex is built the way it is. For the day-to-day architecture
map aimed at code assistants see `CLAUDE.md`; for the outward security policy see `SECURITY.md`;
for feature-level behaviour contracts see `docs/specs/`.

## Goals

- **A local, offline, encrypted store for credentials.** One master password, one file, on
  your machine. No account, no cloud, and no network access except the one carve-out below.
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

## The one network carve-out: checking for updates

Rolodex has exactly one thing that talks to the network, added in ROLO-0037: an **opt-in,
off-by-default** check for a newer release.

This narrows the offline goal above, and the narrowing is deliberate rather than an oversight.
The reasoning is that a security fix nobody installs protects nobody: Rolodex ships binaries
that cannot tell the user a fixed version exists, so users learn about one by visiting GitHub
or not at all.

What keeps it from eroding the goal:

- **Off until the user turns it on.** Rolodex never checks on its own until you enable it, from
  *Check for updates automatically* in the app menu; absent, false or a malformed setting all
  read as off. Choosing *Check for updates...* yourself checks once whether or not that setting
  is on — an explicit click is its own consent, and it is the only route to the network without
  the setting.
- **It sends nothing about you.** A fixed `User-Agent` to the GitHub releases API. No account,
  no identifier, no query string, and nothing derived from the vault. It needs neither the
  vault nor the master password — only `.rolodex.conf` and the app's own version — so nothing
  in it depends on being unlocked, though it is reached from the main window.
- **It cannot install unsigned code.** Every download is verified against an Ed25519 public key
  built into the binary. A download that does not verify is discarded and nothing is installed.
  This is the part that matters: an updater that installs unverified code is a remote-code
  path into the app holding your credentials.
- **It never installs silently.** You are shown what changed and choose Later, Skip This
  Version, or Update Now.

Everything else in this document still holds: no account, no cloud, no sync, and the vault
never leaves your machine. The contract is `docs/specs/ROLO-0037-auto-update.md`.

**One consequence worth stating, because making upgrades easy makes it likelier.**
`migrate_vault()` upgrades a vault's on-disk shape in place and is one-way — there is no
downgrade. An update replaces the binary but never touches `contacts.vault` or `.rolodex.conf`,
so installing one is safe on its own; but once the newer binary opens the vault and migrates
it, going back to an older binary is not supported. Reverting means restoring a backup taken
before the upgrade. This was always true of manual upgrades; an in-app updater simply means
more people reach it.

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
isolation (see `tests/`), and impossible to accidentally couple to a widget's
lifetime. The GUI layer is "dumb": callbacks gather input, call a pure function, and refresh.

**Single-owner persistence.** `MainWindow` holds the only live copy of the decrypted vault,
salt, and password. All mutations funnel through it and end with `self._save()` (re-encrypt
and write the whole file), plus a `self._refresh_list()` when the sidebar contents change.
There is no autosave, no dirty tracking, and no partial writes — the whole vault is the unit of
persistence. This trades write efficiency (irrelevant at this scale) for a design with no
possible desync between memory and disk.

**Off-thread key derivation.** The KDF runs 600,000 iterations, which takes long enough to
freeze the UI. The initial **decrypt** paths — unlock and restore — therefore run the KDF on a
daemon thread and marshal the result back with `GLib.idle_add`. Note that a `_save()`
re-derives the key to *encrypt*, and that currently runs synchronously on the UI thread (so
password-change and every edit briefly block the loop); moving saves off-thread is future work.

## Data model

The decrypted vault is one JSON-serialisable dict with keys `version`, `categories` (an ordered
list), and `entries` (a UUID-keyed map). The literal shape — including the per-field records —
is documented once, canonically, in `CLAUDE.md` (§ Data model); it is not repeated here to
avoid the two copies drifting apart.

Design choices:

- **UUID keys, not names.** Entries are identified by a stable UUID so renaming is free and
  ordering is a pure presentation concern (`list_entries` sorts by name at render time).
- **Ordered category list separate from entries.** Categories exist independently of whether
  any entry uses them, and their order is user-controlled (drag to reorder). An entry
  referencing a deleted category is treated as uncategorised rather than erroring.
- **Three orthogonal axes.** `entry["category"]` is the *user's* grouping;
  `field_category(label)` is a *cosmetic* classifier (credential/key/identity/url/date/other)
  that only picks a border colour; and `sensitive` (mask or not) is a third, separate axis.
  The first two both involve the word "category" but mean different things. Keeping all three
  independent avoids surprising coupling — e.g. a field can be an "identity" colour and still
  be masked.
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
| KDF | PBKDF2 600k (via `cryptography`) | Argon2's memory-hardness (see ROLO-0005) |
| Theme | Bespoke dark CSS | System light/dark following (see ROLO-0011) |
| Recovery | None — password is the only key | Convenience; in exchange, zero server-side attack surface |

## Related documents

- `CLAUDE.md` — architecture orientation for AI assistants.
- `SECURITY.md` — threat model and cryptographic design.
- `docs/security-standards.md` — engineering rules for security-relevant code.
- `docs/specs/` — per-feature behaviour specs.
- `ROADMAP.md` — proposed future work.
