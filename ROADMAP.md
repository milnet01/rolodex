<!-- ants-roadmap-format: 1 -->

# Rolodex Roadmap

Planned and proposed work for Rolodex, grouped by priority. Each item is written to be
spec-ready: a future session can pick one up, write a spec under `docs/specs/`, and implement.

Status legend: 📋 planned · 🚧 in-progress · ✅ shipped · 💭 considered

## High priority

- ✅ [ROLO-0001] **Add an automated test suite for the pure-logic layer.**
  Why: there are zero automated tests today, so every change is verified by hand and refactors are risky.
  Scope: pytest over the GTK-free functions — derive_key/save_vault/load_vault round-trip, migrate_vault idempotency, parse_text_file, search_entries, category helpers. No GUI harness needed because the logic layer is already GTK-free.
  **Layman:** Safety net that checks the encryption and data code still works after any change.
  Kind: test.
  Source: in-session-2026-07-04.
  Seeded (2026-07-04): tests/test_vault.py added with round-trip, wrong-password, 0600, migrate-idempotency, and the save-vault write-error regression. Remaining: parse_text_file, search_entries, category helpers, and CI wiring (ROLO-0020).
  Resolved (2026-07-16): broadened the pure-logic suite to cover parse_text_file, search_entries, the category helpers (add/rename/delete + entries_by_category), and the new generate_password(). CI wiring remains tracked separately as ROLO-0020.

- ✅ [ROLO-0002] **Auto-lock the vault on idle and add a manual Lock button.**
  Why: once unlocked, the vault and master password stay in memory indefinitely — a real gap if the user walks away. This is the biggest security improvement available.
  Scope: a configurable idle timeout that returns to UnlockDialog and clears the decrypted vault + password from memory, plus a toolbar Lock action. Interacts with the session lifetime in MainWindow.
  **Layman:** Re-locks the app after a period of inactivity so an unlocked vault can't sit open.
  Kind: security.
  Source: in-session-2026-07-04.
  Resolved (2026-07-16): configurable idle auto-lock (idle_lock_seconds, default 300s; 0 disables) that wipes vault+salt+password from memory and returns to UnlockDialog. Activity tracked via motion + key EventControllers; plus a header Lock button and Ctrl+L accelerator. Smoke-tested end-to-end.

- ✅ [ROLO-0003] **Clear the clipboard automatically a few seconds after a copy.**
  Why: copied secrets currently sit in the system clipboard until overwritten, readable by any app.
  Scope: after copy_to_clipboard, schedule a GLib timeout that clears the clipboard if its contents are unchanged. Make the delay configurable; show a countdown in the toast.
  **Layman:** Wipes a copied password from the clipboard shortly after, so it doesn't linger.
  Kind: security.
  Source: in-session-2026-07-04.
  Resolved (2026-07-16): a successful copy schedules a GLib timeout (configurable clipboard_clear_seconds, default 20s; 0 disables) that clears the clipboard only when its contents are unchanged (new read_clipboard helper mirrors the wl/xclip/xsel writer priority). Toast shows the clear delay.

- ✅ [ROLO-0004] **Built-in password generator in the add/edit field editor.**
  Why: a credential manager should help create strong secrets, not just store them.
  Scope: a generator control on sensitive FieldRows with length and character-class options, using the secrets module. Pure-logic function generate_password() below the boundary; small popover UI above it.
  **Layman:** A button that fills a field with a strong random password.
  Kind: feature.
  Source: in-session-2026-07-04.
  Resolved (2026-07-16): pure generate_password() using the secrets module — length + per-class toggles, guarantees one char from each selected class. Popover generator control (view-refresh button) on sensitive FieldRows in the add/edit editor; disables Generate when no class is selected. Unit-tested.

- ✅ [ROLO-0018] **Replace the full-sidebar rebuild with a model-backed list and debounced search.**
  Why: _refresh_list() tears down and recreates every sidebar row on every mutation AND on every search keystroke (search_entries scans all entries each time). Fine for a small vault, visibly wasteful for a large one.
  Scope: move to a Gtk.ListView/GtkSelectionModel backed by a data model with incremental updates, and debounce the search-changed handler (e.g. 150ms) so typing doesn't recompute per character. Keep search_entries pure and covered by ROLO-0001 tests. Biggest efficiency win in the app.
  **Layman:** Make the list update smoothly instead of rebuilding the whole thing on every keystroke.
  Kind: perf.
  Source: in-session-2026-07-04.
  Progress (2026-07-17): debounced the search-changed handler (SEARCH_DEBOUNCE_MS=150) so rapid keystrokes coalesce into one rebuild once typing pauses; pending timer is cancelled on lock/close. This removes the per-character rebuild — the efficiency win the body calls out. Verified headlessly: 5 keystrokes -> 1 rebuild; cancel path fires 0. The model-backed Gtk.ListView migration (the larger half) is intentionally deferred pending a go/no-go: after reading the sidebar's surface (3 render modes, collapsible category headers, entry->header drag-and-drop, per-row context menus, ~10 _refresh_list call-sites) it is a high-risk rewrite that is hard to verify without interactive testing on the live vault, and for a personal-scale vault the debounce already resolves the perceived jank.
  Resolved (2026-07-17): closing as shipped on the debounce alone. The 150ms search debounce (SEARCH_DEBOUNCE_MS) removed the per-keystroke rebuild — the efficiency win the body calls out — and is the real perf gain for this app's scale. The model-backed Gtk.ListView migration (the larger half) was evaluated and deliberately DESCOPED, not merely deferred: for a personal-scale vault the debounce resolves the felt jank, while the rewrite is high-risk (3 render modes, collapsible category headers, entry->header drag-and-drop, per-row context menus, ~10 _refresh_list call-sites) and unverifiable without interactive testing on the live vault. If a large vault ever makes incremental updates necessary, open a fresh scoped item for the ListView work rather than reviving this one.

- ✅ [ROLO-0037] **Opt-in, signed in-app auto-update.**
  Requested 2026-08-27, modelled on the finbreak implementation at
  /mnt/Games/Scripts/Linux/finbreak (docs/specs/FIBR-0054.md + FIBR-0131.md).

  Shape: opt-in and OFF by default. On a newer, Ed25519-signed, non-skipped
  GitHub release, offer Later / Skip this version / Update now. Update now
  downloads, verifies the signature, swaps the binary, relaunches.

  Two things Rolodex does not have today and this needs:
  - No __version__ anywhere in the app. .claude/bump.json states this outright
    ("no __version__ in the single-file app"); the version lives only in the
    CHANGELOG heading and the git tag. An updater cannot compare against a
    version the running process cannot read, so this introduces one and the
    bump recipe gains a version-bearing file.
  - No release signing. build.yml attaches unsigned binaries. An updater that
    installs unverified downloads is a remote-code-execution path into the app
    holding the user's credentials, so signing is a prerequisite, not a polish
    item. Ed25519 verification needs no new dependency -- cryptography already
    provides it.

  DESIGN.md states "No account, no cloud, no network access of any kind." This
  feature contradicts that non-goal and the design doc must be amended to carve
  out the opt-in exception rather than leaving the two in conflict.
  Resolved (2026-08-27): implemented and merged. Spec docs/specs/ROLO-0037-auto-update.md
  was gated by review-contract over two cold loops -- 18 findings verified and fixed
  -- and accepted before any code was written.

  Shipped: the opt-in check, the "Check for updates..." menu entry, the
  Later / Skip / Update Now prompt, Ed25519 verification, the same-filesystem
  swap and the wait-for-exit relaunch. Linux and macOS; Windows refused up
  front (ROLO-0042).

  97 tests pass (46 new), ruff clean, Linux build + selftest green. The new
  tests were mutation-checked rather than assumed: four deliberate breaks --
  adding win32 to the asset map, switching asset matching from equality to
  startswith, making the opt-in gate truthy, and swallowing forced errors --
  redden 1, 7, 3 and 1 tests respectively.

  NOT yet functional at the install step, deliberately: the built-in signing
  key is an all-zero placeholder that verifies nothing, so the feature offers
  updates and refuses to install them. ROLO-0041 is the maintainer action that
  completes it. The swap-and-relaunch path needs a frozen binary and two real
  signed releases, so it is untested end to end and says so in the spec.
  Progress (2026-08-27): the DESIGN.md contract gate found a defect in the CODE, after
  this item was already marked shipped. All three cold lanes found the same false
  claim and two traced its cause: check_for_update had one call site passing
  force=True, and set_update_check_enabled had none -- so there was no automatic
  check, no in-app way to set the preference, and INV-1 gated a branch nothing
  reached. Fixed by wiring the silent startup check and a stateful menu toggle,
  plus a source-scan test that reddens on a revert to what shipped. 99 tests.

  Worth remembering: the feature passed its own spec gate, 97 tests and a green
  build while its central preference was inert. What caught it was a cold read of
  a DIFFERENT document, asking whether the design claim was true of the code.
  **Layman:** Let Rolodex tell you when a new version is out and install it for you — off by default, and only if the download is cryptographically signed by us.
  Kind: feature.
  Source: user-request-2026-08-27.

- 📋 [ROLO-0038] **Decide deliberately which of ruff's newer default rules to adopt.**
  ruff.toml now declares select = [E4, E7, E9, F] -- ruff's historical default,
  and the set this codebase was written against. That fixed a CI gate that had
  drifted (see the ruff.toml header), but it also parks 20 real findings that
  ruff 0.16's wider defaults surface. They were deferred, not dismissed:

  - DTZ005 x5 -- datetime.now() without tz. NOT a free fix: created/modified
    are written into the vault as isoformat strings, so making them tz-aware
    changes the on-disk data format and needs a migration decision.
  - BLE001 x5 -- blind `except Exception` in GUI handlers. Mostly deliberate
    (a dialog must not die on an unexpected error), so this is probably a
    per-site noqa rather than a code change.
  - PLW1510 x2 -- subprocess.run without explicit check=. Free fix.
  - RUF012 x1 -- mutable class attribute (SHORTCUTS). Free fix (ClassVar).
  - I001 x3 / RUF100 x4 -- import sorting and now-unused noqa: E402. Both are
    entangled with the gi.require_version() ordering, which MUST run before the
    gi.repository import, so import sorting cannot simply be enabled.

  Each class wants its own decision. Adopting all of them in one sweep would
  breach coding-standards' surgical-change rule.
  **Layman:** Ruff learned some new warnings; decide one by one which are worth keeping rather than taking or ignoring all of them by accident.
  Kind: chore.
  Source: in-session-2026-08-27.

- 📋 [ROLO-0041] **Generate the release-signing key and retire the fail-closed placeholder.**
  ROLO-0037 shipped with an all-zero placeholder public key. It loads and
  verifies nothing, so the updater offers updates and refuses to install any of
  them -- it fails closed rather than open, which is the right interim state but
  is not the finished one.

  This is a MAINTAINER action, not a code change, and it cannot be done by a
  session: the private key must not enter this repository or any transcript.

  1. Run `python3 scripts/gen-signing-key.py`.
  2. Paste the printed public key into rolodex.py's RELEASE_PUBLIC_KEY_B64.
  3. Add the private key as the repository secret ROLODEX_SIGNING_KEY.
  4. Move the private key file somewhere backed up and OUT of the repo.

  `test_INV11_shipped_key_is_the_all_zero_placeholder` will then fail ON PURPOSE.
  Retire INV-11 in the same commit that pastes the key -- that is the test's whole
  job, since a test asserting only "a throwaway signature is rejected" stays green
  whether the placeholder or a real key is shipped and would never notice.

  Until this is done, build.yml's signing step no-ops with a warning and attaches
  no .sig, so the updater makes no offer at all rather than offering something it
  cannot verify.

  Losing the private key later is unrecoverable: shipped binaries will refuse
  every update signed by any other key.
  **Layman:** One manual step by the maintainer turns the update feature from "can look" into "can install".
  Kind: security.
  Source: in-session-2026-08-27.

- 📋 [ROLO-0042] **Extend in-app auto-update to Windows.**
  Deferred from ROLO-0037 (its scope decision S4), and refused up front rather
  than half-working: PLATFORM_ASSETS has no win32 entry, so is_update_supported()
  is False there and no offer is made.

  Windows needs a materially different mechanism. os.replace cannot swap a
  running, locked .exe, and the relaunch goes through /bin/sh, which Windows does
  not have. finbreak solved the same problem with a detached PowerShell helper
  that polls until the image is free, then moves the new binary in and restarts it
  -- see docs/specs/FIBR-0131.md at /mnt/Games/Scripts/Linux/finbreak.

  It also cannot be tested from this machine at all, so the swap and relaunch are
  empirical-only: they need a real two-cycle run on Windows against two signed
  releases. Specify it separately rather than folding it into ROLO-0037's spec.
  **Layman:** Windows users can't yet update from inside the app; the swap has to work differently there.
  Kind: feature.
  Source: in-session-2026-08-27.

- ✅ [ROLO-0043] **Cache the derived Fernet key so saving does not re-run the 600k KDF on the UI thread.**
  save_vault() calls derive_key(), which runs ITERATIONS = 600_000 PBKDF2 rounds. _save()
  calls it from a GTK signal handler on EVERY mutation -- add, edit, delete, field reorder,
  drag-to-category, category rename. That is the identical work the unlock and restore paths
  are deliberately threaded to avoid (master-password.md INV-6).

  Three review lanes reached the same fix independently: derive once at unlock, hold the
  Fernet on MainWindow, and invalidate it only where the salt actually rotates --
  _finish_change_password and _finish_restore. Preferred over moving saves to a background
  thread because it introduces no new secret exposure (the password is already resident for
  the session, per security-standards.md) and no mutation/write race.

  Already acknowledged as a known gap in DESIGN.md and docs/coding-standards.md, which is why
  this is queued rather than fixed in the audit pass: it changes the crypto call path and
  wants its own change with tests.
  Resolved (2026-09-02): implemented as three `_with_key` siblings in the pure
  layer -- save_vault_with_key / load_vault_with_key / create_vault_with_key --
  each doing the real work, with save_vault / load_vault / create_vault kept as
  thin wrappers at their original signatures so no existing caller or test
  changed. MainWindow holds the key derived at unlock in self._key; _save()
  writes through save_vault_with_key. The key is re-derived only where the salt
  rotates (_finish_change_password, _finish_restore) and is cleared alongside
  self.password in _lock and _wipe_secrets_for_update.

  Measured, not estimated: a save went from 81 ms to 0.09 ms (858x). Driven
  headlessly against a real MainWindow, 20 consecutive saves took 3 ms total
  with zero KDF runs, the vault still reloaded under the master password, and a
  password change rotated both salt and key with the vault reopening under the
  new password.

  Four regression tests added to tests/test_regressions.py, and proved red
  against a mutant that puts the KDF back inside save_vault_with_key (2 of the 4
  fail on the mutant, all 4 pass on the fix). Full CI-local green.

  The item's Layman line said "about a second" per edit; the measured figure is
  about 81 ms. Corrected in the trailer rather than left standing.
  **Layman:** Every edit used to pause the window for about a tenth of a second while it re-scrambled your master password. Doing that work once at unlock instead makes saving effectively instant.
  Kind: perf.
  Source: review-code 2026-08-31 lanes 1, 5, 9 (independently).
  Lanes: crypto, gui.

- 📋 [ROLO-0044] **Guard against two Rolodex instances writing the vault last-writer-wins.**
  _save() writes the whole vault with no lock file, no mtime check and no generation counter.
  The single-instance guard added to do_activate() closes the common route to this, but it is
  not a guarantee: it relies on D-Bus registration, and it does nothing about a second
  checkout, a second user, or the vault living on a shared or synced volume.

  The create path compounds it -- is_new is computed once at startup and create_vault ->
  write_private_file -> os.replace overwrites unconditionally, so a vault that appears between
  the check and the write is destroyed.

  Needs a design decision rather than an edit, which is why it is queued: flock on the vault,
  a .lock sidecar, or stat-before-replace with a refuse-or-merge prompt. The atomic-write work
  from 1.3.1 does not address this and was never meant to.
  **Layman:** If two copies of Rolodex ever have the vault open at once, whichever saves last wipes out the other's changes with no warning.
  Kind: fix.
  Source: review-code 2026-08-31 lane 5.
  Lanes: crypto, gui.

- 📋 [ROLO-0045] **Offer restore-from-backup when the vault will not open.**
  do_activate decides create-or-unlock on os.path.exists alone, so a truncated or corrupt
  vault reads as "existing" and the app enters unlock mode. load_vault then raises, the
  unlock dialog shows the error text, and the user is stuck: there is no create path and no
  restore path, because Restore is a MainWindow action that needs an unlocked vault.

  A user with a good backup in Backups/ has no in-app route to it.

  The salt-length check landed in this audit makes the diagnosis honest -- a corrupt vault now
  says so instead of reporting "Wrong password." -- but it does not give the user anywhere to
  go. Offer "Restore from backup..." and "Start a new vault" in the unlock dialog on a
  format/magic error, as distinct from an InvalidToken (wrong password).
  **Layman:** If your vault file gets corrupted, the app just says it cannot open it and there is no way in. Your backups are right there but nothing offers them to you.
  Kind: feature.
  Source: review-code 2026-08-31 lane 9.
  Lanes: gui.

- 📋 [ROLO-0058] **Constrain the update download to GitHub hosts and give it a wall-clock budget.**
  Two verified findings in the same fetch path, neither addressed by the audit pass.

  HOST: asset_url and sig_url come straight from the release JSON and are passed to download_to
  with no constraint beyond _require_https. A tampered or attacker-authored release record can
  point a 250 MB fetch at any TLS host, disclosing the user's IP and User-Agent outside GitHub --
  weaker than INV-3's "the app's only network egress". The Ed25519 check still protects the
  installed bytes, so this is defence in depth rather than an install compromise. Fix: require the
  URL host to end in .github.com / .githubusercontent.com.

  TIMEOUT: UPDATE_TIMEOUT_S is passed as urlopen's timeout, which is per socket OPERATION, not a
  budget for the transfer. A server sending one byte every 29 s holds the connection open
  indefinitely; MAX_UPDATE_BYTES bounds the bytes and nothing bounds the time. INV-9 specifies
  only the socket timeout, so the invariant needs a sentence as well as the code.

  The daemon thread means neither hangs the UI, which is why these are queued rather than urgent.
  **Layman:** The updater will follow a download link to any secure address, and a server that trickles data can keep the download open forever.
  Kind: security.
  Source: review-code 2026-08-31 lane 4 (verified, not fixed in the audit pass).
  Lanes: updater.

- 📋 [ROLO-0059] **Clear the master password and secret values out of their widgets.**
  Three widget buffers keep plaintext alive past the point the code believes it has disposed of
  it:

  - UnlockDialog.pw_entry is never cleared after a successful unlock, so _lock's "Wipe secrets
    from memory" comment is an overstatement -- the same plaintext is still referenced by that
    buffer.
  - RestorePasswordDialog.pw_entry, likewise.
  - FieldRow value entries are not cleared when AddEditDialog closes (neither _commit nor
    _on_close_attempt touches them).

  SECURITY.md puts in-process memory scraping outside the threat model, which is why this is
  queued rather than fixed, and why it is not urgent. It is filed anyway because the code makes
  a claim it does not honour, and the cheap half -- set_text("") on the entries once they have
  been read -- costs nothing.

  Note what this CANNOT fix: a Python str is immutable and cannot be zeroed, so holding the
  password as a bytearray that can be overwritten is the only version of this that fully
  delivers. That is a design decision, not an edit.
  **Layman:** After you unlock, your master password is still sitting in the text box that took it, and a closed edit dialog still holds the secrets you were editing.
  Kind: security.
  Source: review-code 2026-08-31 lanes 5 and 8 (verified, not fixed in the audit pass).
  Lanes: gui.

- 📋 [ROLO-0060] **An interrupted vault write can still leave a temp holding the full ciphertext.**
  write_private_file unlinks its temp on `except Exception`. KeyboardInterrupt and SystemExit are
  BaseException, not Exception, so a Ctrl-C at the wrong moment -- and a power cut, which catches
  nothing at all -- leaves a .rolodex-*.tmp holding the complete ciphertext in the vault
  directory.

  vault-format-and-crypto.md INV-16 claims an interrupted write "leaves no temp behind" for "a
  crash, a full disk, a power cut". Only the disk-full case is actually covered, so the code and
  the invariant disagree in the document's favour.

  Both sides need deciding together, which is why this is queued rather than a one-word edit:
  `except BaseException:` (or try/finally with a success flag) closes the signal case, and the
  power-cut clause in INV-16 has to be narrowed regardless, because no handler can cover it.

  RECORDED AS A CORRECTION: this session's close-findings ledger marked this finding `fixed`.
  It was not -- the handler is unchanged at rolodex.py:120. Filed here so the wrong disposition
  does not stand as the record.
  **Layman:** If the app is killed at exactly the wrong moment while saving, it can leave behind a hidden copy of your whole encrypted vault next to the real one.
  Kind: fix.
  Source: review-code 2026-08-31 lane 1 (verified; a LEDGER CORRECTION -- the audit pass recorded this as fixed and it was not).
  Lanes: crypto, docs.

## Medium priority

- 📋 [ROLO-0005] **Offer Argon2id key derivation with a transparent vault migration.**
  Why: Argon2id is memory-hard and resists GPU/ASIC cracking better than PBKDF2.
  Scope: add an argon2 KDF path, record the algorithm + parameters in the vault header, bump the format version, and re-wrap the vault on next save. migrate_vault gains a KDF-upgrade branch. Requires the argon2-cffi dependency — weigh against the one-file/minimal-deps goal.
  **Layman:** Upgrade the password-scrambling to a newer, tougher method, converting old vaults automatically.
  Kind: security.
  Source: in-session-2026-07-04.

- ✅ [ROLO-0006] **Generate TOTP 2FA codes from stored authenticator secrets.**
  Why: 'authenticator' is already a recognised sensitive keyword; users store 2FA seeds but must go elsewhere to use them.
  Scope: detect otpauth:// or base32 seeds in a field, render a live 6-digit code with a countdown ring, one-click copy. Pure-logic TOTP (RFC 6238) using hmac/hashlib — no new dependency.
  **Layman:** Show the rotating 6-digit login codes right next to the account they belong to.
  Kind: feature.
  Source: in-session-2026-07-04.
  Resolved (2026-07-17): TOTP pure-logic layer (parse_totp_field/totp_code/totp_remaining, RFC 6238) + live "Code" row in _show_detail with countdown ring and one-click copy. 12 new tests (RFC 6238 vectors + detection rule); verified end-to-end against the running app. No new dependency.

- ✅ [ROLO-0007] **Keyboard shortcuts for the common actions.**
  Why: a keyboard-driven tool is faster and expected on the Linux desktop.
  Scope: wire Gtk.Application accelerators — Ctrl+F focus search, Ctrl+N add, Ctrl+L lock (pairs with ROLO-0002), Ctrl+C copy focused field, Escape to clear search. Add a shortcuts window (Ctrl+?).
  **Layman:** Hotkeys like Ctrl+F to search, Ctrl+N for a new entry, Ctrl+L to lock.
  Kind: enhancement.
  Source: in-session-2026-07-04.
  Resolved (2026-07-17): Gtk.Application accelerators wired via win.* actions — Ctrl+F focus search, Ctrl+N add, Ctrl+Shift+C copy secret (plain Ctrl+C left for text selection per user choice), Ctrl+L lock (pre-existing), Esc clears search via SearchEntry stop-search, Ctrl+? opens a hand-built ShortcutsDialog (Gtk.ShortcutsWindow is deprecated in GTK 4.22). Verified: 20/20 pytest + functional checks driving a real MainWindow.

- ✅ [ROLO-0008] **Password health audit: flag weak, reused, and duplicate secrets.**
  Why: storing passwords is only half the value; surfacing bad ones is the other half.
  Scope: a report view scoring sensitive fields on length/variety and detecting reuse across entries. All analysis in the pure-logic layer over the decrypted vault; never leaves the process.
  **Layman:** A checkup screen that points out weak or repeated passwords across your entries.
  Kind: feature.
  Source: in-session-2026-07-04.
  Resolved (2026-07-17): pure-logic password_strength(secret)->0-4 (length + character-class variety; short or single-class is always weak) and audit_passwords(vault)->findings (worst-first, each with strength label + reuse flag; reuse = same secret value in >1 sensitive field). Non-sensitive and empty fields are excluded. A read-only PasswordHealthDialog (built on the new ROLO-0019 make_dialog_scaffold) shows a summary line + a boxed list with Weak/Fair/Good/Strong and Reused chips (Adwaita .error/.warning/.success classes, no new CSS). Opened via a new "Password health..." menu item / win.health action. All analysis in-process. Verified: 4 new unit tests (24/24 pytest), ruff clean, selftest OK, and a headless smoke test (5/5) building the dialog against a mixed weak/reused/strong vault.

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

- ✅ [ROLO-0019] **Extract shared dialog scaffolding and a single 0600 file-write helper.**
  Why: every dialog rebuilds the same ToolbarView + HeaderBar + Clamp boilerplate, the sidebar/field/category lists each hand-roll the same 'remove all rows' loop, and the secure 0600 os.open pattern is duplicated in save_vault and the export path. Duplication invites drift — and the file-write duplication is a security-consistency risk.
  Scope: a small make_dialog_scaffold() helper, a clear_listbox() helper, and one write_private_file() helper used by every secret-writing path. Pure refactor, no behaviour change; lean on ROLO-0001 tests to prove it.
  **Layman:** Tidy up repeated code so the app is easier to maintain and less error-prone.
  Kind: refactor.
  Source: in-session-2026-07-04.
  Resolved (2026-07-17): three helpers extracted. write_private_file(path, data) centralises the 0600 os.open/fdopen dance — used by save_vault and the plaintext export (backup path left as copy2+chmod, a different op). clear_container(widget) replaces the sidebar / detail_box / category-list clear loops (works on ListBox and Box alike). make_dialog_scaffold(dialog, title, *, width, height, clamp_max, margin, scrolled) collapses the ToolbarView+HeaderBar+[scroll]+Clamp boilerplate across all 6 Adw.Dialogs, returning (header, clamp) with each dialog's exact original params so there is zero visual change. UnlockDialog (Gtk.Window) and MainWindow layout untouched. Net -53 lines. Verified: ruff clean, 20/20 pytest, selftest OK, and a headless smoke test that constructs MainWindow + all 6 dialogs (8/8) — each builds with a valid child + title.

- ✅ [ROLO-0020] **Add GitHub Actions CI: lint (ruff) plus the test suite.**
  Why: there is no CI; nothing currently guards a PR. Public repo = free Linux runner minutes.
  Scope: a workflow running ruff (style/lint) and the ROLO-0001 pytest suite on push/PR, on the current stable Python. Pin actions to current major versions per dependency standards. Depends on ROLO-0001 for the test half; the lint half can land immediately.
  **Layman:** Automatic checks on every change so mistakes get caught before merge.
  Kind: chore.
  Source: in-session-2026-07-04.
  Resolved (2026-07-17): added .github/workflows/ci.yml — ruff check + pytest on push/PR to main. Uses the system GTK stack from apt (tests import rolodex.py → gi at module load; a setup-python interpreter lacks gi), mirroring build.yml's install so the two can't drift. actions/checkout pinned to current major v7. The 4 unavoidable E402s (gi imports gated behind gi.require_version) are silenced per-line with a documented reason. Verified: ruff clean, YAML valid, 20/20 pytest. Note: build.yml still on checkout@v4 — separate bump, left out of scope.

- ✅ [ROLO-0021] **Show-password (eye) toggle on sensitive fields in the editor.**
  Why: sensitive field values are hidden while editing with only the 'Hide' checkbox to flip visibility of the whole row; users expect a per-field reveal eye.
  Scope: add a peek toggle to sensitive FieldRow value entries (Gtk.Entry secondary icon) that flips visibility without changing the saved sensitive flag. Small, self-contained UI change.
  **Layman:** An eye icon to peek at what you're typing into a password field.
  Kind: ux.
  Source: in-session-2026-07-04.
  Resolved (2026-07-17): view-only eye toggle added to sensitive value entries in FieldRow via a secondary entry icon; peek never alters the stored sensitive flag. Verified headlessly across masked/peek/hide-off states.

- ✅ [ROLO-0022] **Warn before discarding unsaved changes in the add/edit dialog.**
  Why: Cancel/close on AddEditDialog discards everything with no confirmation — easy to lose work.
  Scope: track a dirty flag on the editor; on cancel/close with changes, show an Adw.AlertDialog to confirm discard. Applies to add and edit.
  **Layman:** Ask 'are you sure?' if you close the editor with unsaved edits.
  Kind: ux.
  Source: in-session-2026-07-04.
  Resolved (2026-07-17): AddEditDialog takes over close via can-close(False)+close-attempt; a form snapshot drives an _is_dirty() check, prompting a Discard confirmation only when edited. Save bypasses via force_close(). Verified: clean dialog closes, dirty dialog stays open.

- ✅ [ROLO-0023] **Warn when a new entry duplicates an existing entry name.**
  Why: the importer detects duplicate names, but manually adding a duplicate is silent — inconsistent and confusing.
  Scope: on save in AddEditDialog for a new entry, if the name (case-insensitive) already exists, prompt to confirm/rename. Reuse the same case-insensitive comparison used by import_entries.
  **Layman:** Flag it when you add an entry with the same name as one you already have.
  Kind: ux.
  Source: in-session-2026-07-04.
  Resolved (2026-07-17): pure find_entry_by_name() (case/whitespace-insensitive, excludes self on edit) + tests; AddEditDialog._on_save prompts a Save-Anyway confirmation on a name collision.

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

- ✅ [ROLO-0030] **Self-contained Linux build (single AppImage, no system dependencies).**
  Why: today Linux users must install GTK4, libadwaita, PyGObject and cryptography from their distro; the user wants a zero-dependency single file.
  Scope: bundle the Python runtime + GTK4/libadwaita + cryptography into one relocatable executable — AppImage (packaging the GNOME platform runtime) or PyInstaller/Nuitka one-file. Ship it as a release asset. The hard part is bundling the GTK stack and its typelib/GObject-introspection data, not the Python. Supersedes part of ROLO-0010 (Flatpak) as the dependency-free distribution path; keep Flatpak for software-center listing.
  **Layman:** A single Linux file you double-click to run — no installing Python or GTK first.
  Kind: package.
  Source: user-request-2026-07-04.
  Linux self-contained binary built and smoke-tested locally via PyInstaller (packaging/rolodex.spec, 72MB single file, GTK4/libadwaita bundled, launches; frozen build persists data to ~/.local/share/Rolodex). CI workflow (.github/workflows/build.yml) builds it on ubuntu-latest. Ships on the first v* tag.
  Shipped in v1.1.0: rolodex-linux-x86_64 (single-file PyInstaller, GTK4/libadwaita bundled). CI builds on ubuntu-latest and gates on a native --selftest before publishing to the GitHub Release.

- ✅ [ROLO-0031] **Self-contained Windows build (single .exe, no dependencies to install).**
  Why: the user wants a Windows version that needs no separate downloads.
  Scope: produce a bundled Windows executable via PyInstaller/Nuitka with the GTK4 + libadwaita runtime from MSYS2/gvsbuild and the cryptography wheel packed in. Major effort: GTK4/libadwaita on Windows is not turnkey (theme, DLLs, GI typelibs, icon themes must all be bundled), and libadwaita's Windows support lags. Investigate feasibility first; if bundling GTK proves impractical, this is the item where a more portable UI toolkit would be evaluated (a large architectural decision, flagged not decided).
  **Layman:** A single Windows .exe that just runs, with everything bundled inside.
  Kind: package.
  Source: user-request-2026-07-04.
  CI job added (.github/workflows/build.yml, windows-latest via MSYS2 UCRT64: gtk4 + libadwaita + python-gobject + cryptography, PyInstaller). Best-effort/untested from the Linux dev box; needs CI-run iteration to confirm the GTK bundle launches on Windows.
  Blocked (2026-07-04): CI builds the .exe on windows-latest (MSYS2 UCRT64) but the binary fails the --selftest gate — it hangs on launch (headless error dialog), meaning the GTK4/libadwaita typelibs/DLLs are not loading from the PyInstaller bundle. Withheld from v1.1.0. Next steps: force-collect the MSYS2 GI typelibs + GTK DLLs + GdkPixbuf loaders in the spec, and confirm on real Windows hardware (a Linux/Wine container does not help — it ships no GTK and Wine != real Windows). Needs a Windows tester for final verification.
  Resolved (2026-07-16): the MSYS2 GTK typelib/DLL bundling was fixed in the spec + workflow and the windows-latest --selftest now passes. Verified green on a GitHub native-runner build of all three OSes. Shipping in v1.2.0 as rolodex-windows-x86_64.exe (self-contained single .exe; unsigned). Build recipe extracted to packaging/windows-build.sh.

- ✅ [ROLO-0032] **Self-contained macOS build (single .app bundle, no dependencies to install).**
  Why: the user wants a macOS version that needs no separate downloads.
  Scope: produce a bundled .app (py2app / Briefcase / PyInstaller) with the GTK4 + libadwaita runtime (Homebrew/jhbuild) and cryptography embedded; code-sign and notarize for Gatekeeper. Same major caveat as the Windows build: GTK4/libadwaita on macOS is non-trivial to bundle and does not feel native. Investigate feasibility; shares the portable-toolkit question raised in ROLO-0031.
  **Layman:** A single macOS app you drag to Applications; everything is inside it.
  Kind: package.
  Source: user-request-2026-07-04.
  CI job added (.github/workflows/build.yml, macos-latest via Homebrew gtk4/libadwaita/pygobject3, PyInstaller, unsigned per user). Best-effort/untested; needs CI-run iteration. Unsigned .app requires right-click->Open past Gatekeeper (no Apple Developer account).
  Shipped in v1.1.0: rolodex-macos-arm64 (unsigned; right-click->Open past Gatekeeper). CI builds on macos-latest via Homebrew and passes the native --selftest gate. Signing/notarization is future work if an Apple Developer account is obtained.

- 📋 [ROLO-0036] **Nothing here says how we would know Rolodex works, outside security.**
  Diagnosed 2026-08-14 by `adopt-project`, run from ~/.claude. Two cold
  readers, five documents each, plus a second pass. No source was read
  and no test was run -- this is not an audit and says nothing about
  code quality.

  **Verdict: state 1**, on ~/.claude/workflow.md's five states. That is
  the discovery state, and it holds however much code exists.

  What the reading found:

  - **What it is for: YES**, stated and quotable, non-goals included.
  - **How we would know it works: NO** -- for one half of the purpose.

  `SECURITY.md`'s threat model **does** state judgeable outcomes, and the
  reader was right to count them. But they cover security alone, and the
  stated purpose begins *"A safe, **simple** place..."*. There is no
  criterion for usability, speed or durability anywhere.

  The seven specs do not fill the gap. They are explicitly *"retroactive
  ... extracted from the shipped code"*, so every invariant in them
  describes what **was built** rather than what would count as **working**.
  A spec written backwards from the code cannot fail.

  **What would close this.** Success criteria for the "simple" half --
  what a first-time user must be able to do, and how fast. Discovery is
  a conversation, not a writing task, so this is not a doc someone
  drafts alone.

  Recorded rather than acted on: the diagnosis was produced elsewhere,
  and what to do about it is this project's call.
  **Layman:** The security side has real, judgeable goals. The "simple to use" half of the promise has none, so nothing can tell us whether we delivered it.
  Kind: doc.
  Source: adopt-project-run-2026-08-14 (from ~/.claude).

- 📋 [ROLO-0039] **Publish release notes from CHANGELOG.md instead of an empty body.**
  build.yml's "Attach to Release" step uses softprops/action-gh-release@v3 with
  `files:` only -- no `body` and no `body_path`. So a v* tag creates the GitHub
  Release with an EMPTY body. Found on v1.3.1, which published with no notes at
  all and had to be corrected by hand with `gh release edit --notes-file`.

  releases.md section 5 wants the notes to be the changelog section verbatim.
  The fix is to extract that section in the workflow and pass it as body_path,
  so the release page, the annotated tag and CHANGELOG.md all carry one text.

  Note the matrix runs the attach step three times (once per OS). Whichever job
  lands first creates the release, so the body must be supplied identically by
  all three, or supplied by a separate single-run job that the three attach to.
  The second shape is the safer one -- three jobs racing to set a body is how
  they come to disagree.

  Also worth pinning while in there: the action is on a mutable major tag (@v3)
  rather than a commit SHA.
  **Layman:** When a new version is published, the release page should show what changed instead of being blank.
  Kind: fix.
  Source: in-session-2026-08-27.

- 📋 [ROLO-0046] **Run the clipboard helpers off the GTK main thread.**
  copy_to_clipboard and read_clipboard both use subprocess.run(..., timeout=5) and are called
  from a button click and from a GLib timeout. A hung wl-paste freezes the UI for 5 s, or 10 s
  across _clear_clipboard_if_unchanged's read-then-write.

  The project already keeps the KDF and the update fetch off the main thread for exactly this
  reason. Either move both through a short-lived thread with GLib.idle_add, or drop the
  external tools for the async Gdk.Clipboard API -- which would also remove the per-platform
  tool list the macOS/Windows fix had to extend.
  **Layman:** Copying a password shells out to a helper program with a five-second limit, and it does that on the thread that draws the window — so if the helper hangs, the app freezes.
  Kind: fix.
  Source: review-code 2026-08-31 lane 6.
  Lanes: gui.

- 📋 [ROLO-0047] **Settle whether a re-ticked duplicate should import, and make preview and commit agree.**
  Two lanes found the same disagreement from opposite ends. import_entries dedups against the
  vault AND within the import file; ImportPreviewDialog computes its duplicate set from the
  vault only, so two identically-named entries in one file both render unmarked and
  pre-checked, and one is then silently skipped.

  Separately, _finish_import passes skip_duplicates=True, so a duplicate the user deliberately
  re-ticks is discarded -- the checkbox has no effect for exactly the rows it draws attention
  to.

  import-export-backup.md contradicts itself here: INV-7 says "only checked entries import",
  which reads as a promise that checked entries DO import, while its own Notes record the
  skip_duplicates behaviour. The spec needs deciding before the code moves, so this is a
  review-contract item first. The preview is the user's consent surface for a bulk write into
  an encrypted vault; whichever way it is settled, it must not show one thing and do another.
  **Layman:** The import preview lets you tick a duplicate entry, then throws it away anyway. Either the tick should work or it should not be offered.
  Kind: doc-fix.
  Source: review-code 2026-08-31 lanes 3 and 8.
  Lanes: import-export.

- 📋 [ROLO-0048] **Store entry timestamps with a timezone.**
  add_entry and update_entry write datetime.now().isoformat() -- naive local time. Across a
  timezone change or a DST fall-back, `modified` can precede `created`, and the detail pane
  renders the value with no offset marker.

  entries-and-fields.md INV-1 says only "ISO-8601 strings", which this technically satisfies,
  so the spec needs a sentence too. The fix itself is datetime.now().astimezone().isoformat(),
  but it is queued rather than done inline because existing vaults hold naive values: readers
  must tolerate both forms, and whether to rewrite old timestamps on migration is a decision,
  not an edit.
  **Layman:** Saved-at times have no timezone, so a vault carried to another country — or across a clock change — shows edits in the wrong order.
  Kind: fix.
  Source: review-code 2026-08-31 lanes 3, 6, 7.
  Lanes: data-model.

- 📋 [ROLO-0049] **Declare the GTK and libadwaita minimum versions the code already requires.**
  Two hard floors are used with no minimum stated in README.md, requirements.txt, the
  packaging scripts or the workflows:

  - Gtk.CssProvider.load_from_string (do_startup) is GTK 4.12+. Debian bookworm ships 4.8, so
    this is an AttributeError before any window appears. Using load_from_data(CUSTOM_CSS.encode())
    instead would drop the floor to 4.0.
  - Adw.Dialog, set_can_close, close-attempt, force_close and Adw.AlertDialog are libadwaita
    1.5+. On a distro shipping 1.4 the whole editor stack fails at attribute lookup.

  Verify both floors against the real API history before writing them down -- the lanes read
  them from usage, not from a compatibility table.
  **Layman:** The app needs fairly recent versions of its UI libraries but never says so, so on an older Linux it fails at startup with a confusing error.
  Kind: doc.
  Source: review-code 2026-08-31 lanes 8 and 9.
  Lanes: packaging.

- 📋 [ROLO-0050] **Bound the size of a chosen import file.**
  parse_text_file reads the whole user-chosen file into memory and re.splits the entire string.
  A mis-picked multi-GB file is an OOM rather than a message. The regex itself is not
  ReDoS-prone -- [^:]+? cannot overlap the literal colon, so backtracking is linear.

  Queued rather than fixed because the ceiling is a number somebody has to choose, and it
  should be stated in import-export-backup.md rather than only in the code.
  **Layman:** Picking a huge file by mistake in the import dialog will make the app run out of memory and die, with no message.
  Kind: security.
  Source: review-code 2026-08-31 lanes 3 and 7.
  Lanes: import-export.

- 📋 [ROLO-0051] **Guard the update check and download against re-entry.**
  _on_check_updates and _start_update_download take no re-entrancy guard, so repeated menu
  clicks spawn N threads and N offer dialogs, and two accepted offers give two concurrent
  downloads racing on the same os.replace.

  The INV-15 cancellation flag added in this audit makes the teardown correct but does not make
  the start path idempotent -- a second download still begins. Wants a simple in-flight flag on
  MainWindow, plus disabling the menu item while a check or download is running.
  **Layman:** Clicking “Check for updates” repeatedly starts a new check each time, and accepting two update offers downloads twice at once.
  Kind: fix.
  Source: review-code 2026-08-31 lane 6.
  Lanes: updater.

- 📋 [ROLO-0052] **Turn on mypy's untyped-def checking and annotate the public signatures it then reports.**
  coding-standards.md requires type hints on every public function signature. mypy runs clean
  because unannotated defs are UNCHECKED by default -- so roughly twenty functions are
  reported as passing without being analysed at all.

  Three review lanes independently found unannotated public signatures and independently tagged
  it a tool gap, which is the strongest signal in the run that the checker is not doing what
  the standard assumes.

  Two parts, in order: add the annotations, then enable --disallow-untyped-defs so it cannot
  regress. Doing it the other way round turns the whole file red at once. This is check-code's
  tool set, not the project's CI gate, so it also wants recording wherever that calibration
  lives.
  **Layman:** The type checker is skipping about twenty functions because they have no type labels, so it reports the code as clean without having looked at them.
  Kind: chore.
  Source: review-code 2026-08-31 lanes 3, 4, 8 (tool gap).
  Lanes: tooling.

- 📋 [ROLO-0057] **Run review-contract over the four spec claims this audit falsified.**
  Four document-side findings, each verified against source, none fixable without deciding
  which side is authoritative. Grouped because they want one review-contract pass, not four.

  1. entries-and-fields.md INV-9 -- "the value entry's visibility always tracks the Hide
     checkbox, so a field is never shown in cleartext while it will be saved as sensitive" is
     falsified by the peek toggle (ROLO-0021), which is a deliberate, commented feature. The
     DOCUMENT is the wrong side. Its Notes are also stale: they call built-in password
     generation a roadmap item, and it shipped.

  2. ROLO-0037 D5 -- says the update entry is "disabled with a tooltip when
     is_update_supported() is False". The disable is there; a Gio.Menu model item cannot carry a
     tooltip in GTK4, so the promise is unimplementable as written. The DOCUMENT is the wrong
     side; the manual action already compensates with an explanatory dialog.

  3. ROLO-0037 D5 again -- justifies keeping the update preferences out of the vault "because
     the check runs at startup while the app is locked", and INV-4 says it "runs correctly while
     the app is locked". The invariant holds (no vault dependency), but the check is scheduled
     from MainWindow.__init__, which only exists AFTER a successful unlock. So a user who leaves
     Rolodex at the lock screen is never told about a release. Cannot tell from the documents
     which side is intended.

  4. vault-format-and-crypto.md INV-16 -- claims an interrupted write "leaves no temp behind"
     for "a crash, a full disk, a power cut". write_private_file unlinks only on `except
     Exception`, so a KeyboardInterrupt/SystemExit (both BaseException) or a power cut leaves a
     .rolodex-*.tmp holding the full ciphertext in the vault directory. Only the disk-full case
     is actually covered. Either narrow the claim or widen the handler.

  Also for that pass: INV-15's test surface reads "none -- GTK-layer behaviour, verified by
  hand", and two lanes independently found its teardown clause had no implementing code at all.
  Whatever that hand-verification exercised, it was not this. The clause is implemented as of
  this audit; the spec should say how it is now checked.
  **Layman:** Four places in the design documents now describe behaviour the code does not have. Each needs a decision about which side is wrong.
  Kind: doc-fix.
  Source: review-code 2026-08-31 lanes 2, 4, 8, 9.
  Lanes: docs.

- 📋 [ROLO-0061] **Write the missing specs for five shipped features that have no contract at all.**
  docs/specs/ holds six feature specs. These five shipped features have NONE, so a reviewer has
  no contract to judge them against and a change has nothing to conform to:

  - ROLO-0002 auto-lock on idle + manual Lock. Lane 5 judged the activity-tracking defect
    against a CODE COMMENT because there was nothing else, and that comment turned out to be
    the thing that was wrong.
  - ROLO-0003 clipboard auto-clear. Lane 6 could not tell whether "a lock must clear the
    clipboard" was a requirement or its own inference, and had to say so rather than rule.
  - ROLO-0004 built-in password generation.
  - ROLO-0006 TOTP / live 2FA codes -- the largest gap. Its whole contract today is six lines of
    CLAUDE.md, one line of DESIGN.md and a ROADMAP entry, so the digit range, the period bounds,
    the >=80-bit floor and the label-keyword list were all reviewed against the RFCs alone.
  - ROLO-0021 the peek/eye toggle, which is the feature that falsifies entries-and-fields.md
    INV-9 (see ROLO-0057).

  Password health (audit_passwords) is a sixth: it is not a roadmap feature of its own but it is
  user-visible, and lane 3 could not settle whether "duplicate" and "reused" are two checks or
  one because nothing states it.

  Each wants spec-format.md's §1 test applied first -- several may honestly not need a full
  spec, and that is a legitimate outcome. What is not legitimate is the current state, where
  nobody has asked.
  **Layman:** Five features that are finished and in use were never written down anywhere, so there is nothing to check them against when they change.
  Kind: doc.
  Source: review-code 2026-08-31 lanes 2, 3, 6 (each hit the gap independently).
  Lanes: docs.

- ✅ [ROLO-0062] **Run review-contract over docs/security-standards.md §2, which this audit rewrote.**
  The audit pass rewrote security-standards.md §2. It had prescribed
  `os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` for every secret write -- the pre-1.3.1 form,
  which a contributor following it literally would have used to reintroduce the non-atomic write
  and silently undo INV-16. It now routes every secret write through write_private_file, adds
  the explicit ban on shutil.copy2-then-chmod, and adds a clause for the signing key as a fourth
  class of secret file. The project CLAUDE.md carried the same stale claim and was corrected in
  the same commit.

  Why this is filed rather than closed: security-standards.md is a STANDARD, and under global
  CLAUDE.md rule 14 the test is "would someone conforming to this document now do something
  different?" -- which for this edit is plainly yes. That makes it a direction change owing a
  `review-contract <path> --genre standard` cold read, and that gate has NOT been run. The
  commit body records the same thing.

  The edit is believed correct and is strictly better than what it replaced; this is about the
  gate being owed, not about doubting the content.
  Resolved (2026-09-02): gate run as `review-contract docs/security-standards.md
  --genre standard`. Three loops, three cold lanes each, cap reached.
  **Twelve verified findings, twelve fixed, one dismissed and recorded.**

  Not one Q1 across three loops -- every factual claim the standard makes about
  the code held, including four the lanes could not settle and the orchestrator
  resolved by opening the code. What the document got wrong was internal
  coherence, and two findings could only be settled by RUNNING something:
  applying rule 2's helper to a signing key would silently destroy it
  (write_private_file overwrote an existing file; O_EXCL raised FileExistsError),
  and the retired O_TRUNC form cost the permission guarantee as well as
  atomicity (re-opening a 0644 file with mode 0o600 left it 0644).

  The sharpest finds: rule 2 forbade shutil.copy2 + chmod while citing a spec
  that prescribed it (all three lanes, loop 1); rule 1 forbade temp files holding
  field values while rule 2 mandated one for the plaintext export (two lanes,
  loop 2); and the merge checklist twice tested something weaker than the rule it
  gates, so a reviewer could tick the box on the exact code the rule forbids.

  Collateral corrected in four neighbouring documents rather than carried into
  the subject: vault-format-and-crypto.md INV-5 and INV-9,
  import-export-backup.md INV-9 and INV-15, SECURITY.md's permissions table (the
  outward-facing document this standard says must agree with it), and
  dependency-management-standards.md's pin exception.

  Cap verdict: two of loop 3's three findings landed on text this run wrote, so
  by the measure it is a violent cap -- the review of the document as it stands
  ends here and a fourth loop is not filed. Size is not the signal; the subject
  is 105 lines. About a quarter of the twelve findings anchor inside the span
  this item named, so the run was mostly audit rather than gate.

  Loop log: docs/review-2026-09-02-security-standards.md. Two orchestrator packet
  defects are recorded there honestly -- both were mis-cut source windows, and in
  both cases every lane routed the discrepancy to Open questions rather than
  filing a false finding.
  **Layman:** A rule book that told developers to do the wrong thing was corrected, but the correction has not itself been reviewed.
  Kind: doc-fix.
  Source: close-findings 2026-08-31 (surfaced by the sweep; recorded in commit 5db68d2).
  Lanes: docs.

- 📋 [ROLO-0063] **Preference and config writes report success when the write was swallowed.**
  _on_toggle_auto_updates calls set_update_check_enabled(enabled) and then unconditionally
  toasts "Rolodex will check for updates on startup". save_config swallows OSError by design, so
  a preference that failed to persist is reported to the user as set, and the app is not in fact
  checking for updates.

  ROLO-0037 INV-7 licenses the silent drop for the SKIP-a-version case explicitly -- "a skip that
  cannot be written is dropped silently" -- and says nothing about the opt-in itself, which is the
  one preference the whole feature's security value depends on.

  The audit pass made save_config atomic, which removes the truncation route to losing it, but
  not this one: a read-only directory or a full disk still fails silently. Fix: have save_config
  report success, and toast the failure for the opt-in specifically.
  **Layman:** Turning on automatic update checks says it worked even when the setting could not be saved — so it silently goes back to off.
  Kind: fix.
  Source: review-code 2026-08-31 lane 6 (verified, not fixed in the audit pass).
  Lanes: updater, gui.

- 📋 [ROLO-0064] **The plaintext export stages its temp in the directory the user picked.**
  write_private_file stages `.rolodex-*.tmp` in the DESTINATION directory, which is correct and
  necessary for the vault (INV-10 requires a same-filesystem os.replace) but is a different
  trade for the plaintext export, whose destination is wherever the user pointed the file
  chooser -- plausibly a cloud-synced or shared folder.

  The temp is created 0600 and unlinked on the way out, so the exposure is narrow. A SIGKILL or
  a power cut leaves decrypted credentials as a dotfile in that directory, and ROLO-0060's
  BaseException gap widens the window to include Ctrl-C.

  Two defensible answers and the choice is the work: document it in
  import-export-backup.md as accepted, or accept a non-atomic 0600 os.open for the export alone,
  where atomicity buys much less than it does for the vault.
  **Layman:** Exporting your passwords briefly writes a hidden copy into whatever folder you chose — which might be a shared or cloud-synced one.
  Kind: security.
  Source: review-code 2026-08-31 lane 7 (verified, not fixed in the audit pass).
  Lanes: import-export.

- 📋 [ROLO-0065] **Two diverged implementations of “same name, case-insensitively”.**
  import_entries compares `e["name"].lower()`; find_entry_by_name compares
  `name.strip().lower()`. ROADMAP ROLO-0023 explicitly directed the second to "reuse the same
  case-insensitive comparison used by import_entries", and it added .strip() instead.

  Unreachable through normal paths today because both writers strip before storing, so it bites
  only a hand-edited, legacy or restored vault holding an untrimmed name -- which is exactly the
  population ROLO-0060 and the migrate_vault guard are about. Fix: one helper, both callers.
  **Layman:** Two places in the code decide whether two entries have the same name, and they disagree about spaces.
  Kind: fix.
  Source: review-code 2026-08-31 lane 3 (verified, not fixed in the audit pass).
  Lanes: data-model.

- ✅ [ROLO-0066] **Decide what “reused” means in the password health report.**
  audit_passwords counts occurrences of a value across all sensitive fields, so an entry holding
  "Password" and "Password (recovery copy)" with the same value is reported "Reused x2" though
  the secret is used in exactly one place.

  The code matches its own docstring and ROADMAP ROLO-0009's wording -- "more than one sensitive
  field" -- so this is the CONTRACT being questioned rather than the implementation being wrong.
  Counting distinct entry_ids per value is the alternative.

  Lane 3 also could not settle whether the lane contract's "duplicate detection" is a third check
  or a second word for reuse; there is no mechanism for a third. Settling both belongs with the
  missing password-health spec.
  Resolved (2026-09-02): settled by the user as "reuse means across entries".
  audit_passwords now counts distinct entry ids per secret value rather than
  sensitive fields, so two fields inside one entry -- a password and its backup
  password -- are one account and are not flagged. reuse_count changes meaning
  with it, from fields to entries; it is returned but never displayed, and the
  only consumer of the finding is the boolean. Two regression tests added and
  proved red against a mutant that restores per-field counting. The pre-existing
  test_audit_passwords_detects_reuse_across_entries still passes unchanged.
  **Layman:** The health report can tell you a password is “reused twice” when it is only stored once, in two fields of the same entry.
  Kind: investigate.
  Source: review-code 2026-08-31 lane 3 (verified; a contract question, not a defect).
  Lanes: data-model.

- 📋 [ROLO-0067] **Imported entries always land uncategorised, and nothing says whether that is intended.**
  import_entries calls add_entry with name, fields and notes and never passes a category, so
  every imported entry gets "". Neither import-export-backup.md nor categories.md says whether
  that is the intended behaviour or an omission.

  Small on its own; filed because it is a promise nobody made either way, and the import preview
  (ROLO-0047) is being revisited anyway -- offering a target category there would be the natural
  place if the answer is that it should be settable.
  **Layman:** Importing from a text file drops every entry into no category at all.
  Kind: fix.
  Source: review-code 2026-08-31 lane 3 (verified; unstated in either spec).
  Lanes: import-export.

- 📋 [ROLO-0068] **TOTP robustness: unvalidated direct-call arguments, and no clock-skew signal.**
  Two items from the same lane.

  VALIDATION: totp_code's digits, period and algorithm are validated in _parse_otpauth_uri and
  nowhere else. _TOTP_HASHES[algorithm] raises KeyError, `// period` raises ZeroDivisionError,
  and struct.pack(">Q", counter) raises struct.error for a negative counter (a system clock set
  before 1970). Latent rather than live -- both real call sites receive a validated config -- so
  this is a guard on a public function, not a bug fix.

  CLOCK SKEW: RFC 6238 §6 requires prover and validator to agree on time. A machine whose clock
  has drifted emits wrong codes with no user-visible indication, and it presents to the user as
  "the site rejected my code" with nothing pointing at the real cause. The fix is UI-side -- a
  hint in the detail pane when the system clock looks unsynchronised -- and wants stating in the
  missing TOTP spec first.
  **Layman:** If your computer's clock drifts, your 2FA codes are silently wrong and the app gives no hint why.
  Kind: fix.
  Source: review-code 2026-08-31 lane 2 (verified, not fixed in the audit pass).
  Lanes: totp, gui.

- ✅ [ROLO-0079] **Decide whether the 8-character master password floor is still the right one.**
  MIN_PASSWORD_LENGTH = 8. The master password is the ONLY protection on a file an attacker can
  brute-force offline as fast as their hardware allows -- SECURITY.md says so itself, and there is
  no recovery path.

  NIST SP 800-63B permits an 8-character floor for memorized secrets, so this is not a breach;
  OWASP ASVS V2.1.1 requires 12, and this is a key-derivation passphrase rather than a
  server-side credential with rate limiting behind it. security-standards.md §3 pins ITERATIONS
  and the salt size and states no password minimum at all, so nothing is currently violated --
  the floor is simply low and undocumented.

  If raised: apply it to NEW vaults and password changes only (rolodex.py's two validation sites),
  and leave existing vaults unlockable -- locking someone out of their own data to enforce a
  policy change is worse than the policy gap. Whichever way it is settled, it belongs in
  security-standards.md §3 rather than only in a constant.
  Resolved (2026-09-02): MIN_PASSWORD_LENGTH raised from 8 to 12 on the user's
  call, per OWASP ASVS. Verified first that this locks nobody out: the constant
  gates only vault creation (rolodex.py:1360) and the change-password dialog
  (rolodex.py:3404), plus the generator's lower slider bound; unlock never
  re-checks length, so an existing vault with a shorter password still opens.
  master-password.md INV-2/INV-10 and the README's first-launch line updated in
  the same change. Locked by a floor test in the shape of the KDF iteration
  floor -- the value may be raised, never lowered below 12.
  **Layman:** The shortest master password Rolodex accepts is 8 characters, which is on the low side for the one password protecting everything.
  Kind: security.
  Source: review-code 2026-08-31 lane 1 (verified, not fixed in the audit pass).
  Lanes: crypto.

- 📋 [ROLO-0080] **Structural hardening of the updater's HTTP layer.**
  Two observations from the same lane, neither a live defect, both worth recording so they are
  not rediscovered.

  - _opener() uses urllib.request.build_opener, which still registers the default HTTPHandler.
    So plain http:// remains reachable AT THE URLLIB LAYER and only _require_https keeps it out.
    Passing an explicit handler set would make the https-only property structural rather than
    enforced by a check that a future edit could bypass. The check itself is correct today and is
    re-applied on every redirect hop (INV-9).
  - download_and_verify reads the whole asset (up to MAX_UPDATE_BYTES = 250 MB) into RAM to
    verify it. cryptography's Ed25519 needs the full message, so a streaming verify is not
    available, and the cap is what sizes the peak -- this is recorded as a known consequence
    rather than something to fix. Revisit only if the cap is ever raised substantially.

  Also noted: _opener() rebuilds an SSLContext and re-parses certifi's CA bundle on each of the
  three requests a check-and-download makes. Cheap to cache, and purely a cost item.
  **Layman:** The update code disallows insecure connections by a check rather than by construction, and holds an entire downloaded file in memory to verify it.
  Kind: security.
  Source: review-code 2026-08-31 lane 4 (verified; defence-in-depth, not live defects).
  Lanes: updater.

- 📋 [ROLO-0081] **Run review-contract over the two documents this session gave new rules: the vault spec and the dependency standard.**
  ROLO-0043 added INV-17 to docs/specs/vault-format-and-crypto.md: the KDF runs
  once per credential rather than once per save, the `_with_key` siblings carry
  an already-derived key, a key must only be written alongside the salt it was
  derived from, and the derived key is cleared wherever the password is.

  Under global CLAUDE.md rule 14 the test is "would someone conforming to this
  document now do something different?" -- and the answer is plainly yes: a new
  save path written against the spec before this edit would call save_vault and
  silently reintroduce the defect ROLO-0043 removed. That makes it a direction
  change owing `review-contract docs/specs/vault-format-and-crypto.md
  --genre spec`, which has NOT been run.

  The same commit also added a matching convention bullet to the project
  CLAUDE.md, which is a built-under document and carries the same obligation.

  Two corrections landed in the same commit and are NOT what this item is about,
  since neither changes what a conformer builds: the spec's Notes said the
  password-change handler "calls `_save`" when it has always written directly so
  it can order the write before adopting new credentials, and CLAUDE.md placed
  the pure/GUI boundary at "roughly the first 590 lines" when the banner sits far
  below that.

  The edits are believed correct; this is about the gate being owed.
  Progress (2026-09-02): a second document joins this item.
  `docs/dependency-management-standards.md` gained a precedence rule during
  ROLO-0062's loop 3 -- its forced-older-pin exception now says outright that it
  cannot take a dependency below a security floor, naming
  `security-standards.md`'s `cryptography >= 44.0.0`.

  That is a direction change for anyone reading only that document: before it,
  a conformer facing a break fixable at 43.x would pin there with an inline
  reason and a ledger row, exactly as that standard's process instructs, and ship
  a crypto library with known CVEs. So it owes
  `review-contract docs/dependency-management-standards.md --genre standard`,
  which has NOT been run.

  Both documents on this item are believed correct. What is owed is the gate.
  **Layman:** A rule about how the vault must be saved was added to the design document, and that addition has not itself been reviewed by a fresh pair of eyes.
  Kind: doc-fix.
  Source: in-session-2026-09-02 (ROLO-0043 implementation).
  Lanes: docs, crypto.

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

- ✅ [ROLO-0033] **Document the project's versioning standard (SemVer).**
  Adds docs/versioning-standards.md (MAJOR.MINOR.PATCH per SemVer, with Rolodex-specific rules for what counts as each — the vault format is the breaking-change hinge) and links it from CONTRIBUTING.md's Standards list. Formalises the practice the CHANGELOG already claimed ('aims to follow Semantic Versioning').
  **Layman:** Write down the rule for version numbers so it's clear when to bump the big, middle, or last number.
  Kind: doc.
  Source: user-request-2026-07-17.

- 📋 [ROLO-0034] **Track and cancel the clipboard auto-clear timer on lock/close.**
  The clipboard auto-clear GLib.timeout (ROLO-0003) is fire-and-forget: its source id is never stored, so it isn't cancelled when the vault locks or the window closes. Harmless today (the callback clears the clipboard safely regardless), but fragile if the callback ever grows to touch window state. Track the source id alongside the TOTP/search timers and cancel it in the same lock/close paths. Surfaced by the 2026-07-17 debt sweep (source-audit lane).
  **Layman:** Tidy up a background timer so it can't fire after the window it belongs to is gone.
  Kind: refactor.
  Source: debt-sweep-2026-07-17.

- 📋 [ROLO-0035] **Fill remaining info-level pure-logic test gaps (derive_key KAT, delete_entry, list_entries).**
  The 2026-07-17 debt sweep added tests for field_category, load_vault magic-byte reject, is_sensitive_label, import_entries, parse_text_file edge rules, update_entry/rename_category timestamp behaviour, tampered-ciphertext, create_vault, and atomic write_private_file (suite 36 -> 49). Remaining info-level gaps deferred: a derive_key known-answer test on a fixed salt/password (locks the exact PBKDF2 contract), and trivial delete_entry / list_entries coverage.
  **Layman:** A few more small safety-net tests for the least-risky helper functions.
  Kind: test.
  Source: debt-sweep-2026-07-17.

- 📋 [ROLO-0040] **Run the contract gate on documentation-standards.md.**
  Two rules were added to the Style section on 2026-08-27 at the user's
  instruction: don't write counts or line numbers, and every claim must be
  checkable.

  That changes direction for anyone writing docs under this standard -- a
  conformer now writes "the offer table" where they would have written "the
  ten-case table" -- so CLAUDE.md rule 14's test answers yes and the cold-read
  gate is owed on it.

  It was NOT run, deliberately and not silently. A review-contract run was
  already in flight on the auto-update spec, and that skill's Phase 1a forbids
  widening a run's subject mid-flight; starting a second concurrent gate was
  judged worse than filing this. Run `review-contract
  docs/documentation-standards.md --genre standard` (cap 3 for a standard).
  **Layman:** A rule change to the writing standard is owed an independent read-through that has not happened yet.
  Kind: doc.
  Source: in-session-2026-08-27.

- 📋 [ROLO-0053] **Make field and category reordering reachable from the keyboard.**
  The drag handle is a Gtk.Image, which is not focusable, and no accelerator or move action
  exists. entries-and-fields.md INV-7 and categories.md INV-13 both promise reordering as a
  feature; for a keyboard-only or motor-impaired user the feature does not exist.

  Ctrl+Up / Ctrl+Down on the focused row calling the existing _reorder_field / _reorder_category
  is the cheap version. Several icon-only buttons across the app also carry tooltip_text but no
  accessible name, and the category count badge is a bare numeral -- worth doing in the same
  pass.
  **Layman:** You can only reorder fields and categories by dragging them with a mouse, so anyone using just a keyboard cannot do it at all.
  Kind: accessibility.
  Source: review-code 2026-08-31 lane 8.
  Lanes: gui.

- 📋 [ROLO-0054] **Give the project a .yamllint so the linter measures its own style.**
  yamllint has no project config, so it runs on defaults the project never adopted: 26 of the
  32 findings are the 80-column limit (coding-standards.md declares ~100, and that rule is
  Python-scoped anyway -- only one line in the tree exceeds 100), two are the GitHub Actions
  `on:` key flagged as non-truthy, and the rest are document-start and comment spacing.

  As it stands the tool produces noise on every run and decides nothing, so its output is
  skipped rather than read -- which is how a real finding would be missed. A .yamllint stating
  the project's actual line length and disabling the truthy and document-start rules for
  workflows would make a yamllint finding mean something.

  NOT a suppression of a rule that caught something: no finding here is a defect.
  **Layman:** The YAML checker currently complains about 32 things using its own default rules, none of which this project ever agreed to.
  Kind: chore.
  Source: check-code 2026-08-31 (whole-tree).
  Lanes: tooling.

- 📋 [ROLO-0055] **Decide whether zizmor should run at the auditor persona in the audit sweep.**
  check-code runs zizmor at its default `regular` persona, which suppressed 9 of 17 findings on
  this tree. Lane 10 reported excessive-permissions on build.yml's workflow-wide `contents:
  write`; that audit does not emit at the default persona, and a re-run at --persona=auditor
  confirmed it, plus template-injection x4, secrets-outside-env and concurrency-limits.

  The permissions finding is now fixed by the build/sign job split, so this is about the
  instrument rather than the tree: at the default persona the sweep could not have found it.
  The auditor persona is noisier -- the template-injection hits are ${{ matrix.asset }} in run:
  blocks, which is workflow-controlled and not attacker-controllable -- so this is a calibration
  decision, not an obvious yes.
  **Layman:** The workflow security scanner has a stricter mode that is turned off by default, and it spots things the default mode stays quiet about.
  Kind: chore.
  Source: review-code 2026-08-31 lane 10 (tool gap).
  Lanes: tooling.

- 📋 [ROLO-0056] **Replace softprops/action-gh-release with a gh release script step.**
  zizmor's superfluous-actions audit (informational) points out that `gh release upload` in a
  run: step does what softprops/action-gh-release@v3 is being used for, using the CLI already
  present on the runner.

  Worth doing mainly to remove a third-party action from the job that now holds the signing key
  and contents: write -- one fewer upstream in the blast radius. The action is hash-pinned as of
  this audit, so it is not urgent.
  **Layman:** The release step uses a third-party add-on to do something the runner can already do by itself.
  Kind: chore.
  Source: check-code 2026-08-31 (zizmor superfluous-actions).
  Lanes: packaging.

- 📋 [ROLO-0069] **Password generation over-weights the smaller character classes.**
  generate_password guarantees one character per enabled class and fills the rest from the
  combined pool. That construction over-represents the smaller classes: at the default 20
  characters over an 85-character pool, expected digits are about 2.9 against 2.35 under uniform
  sampling.

  Explicitly NOT a security defect -- the entropy loss is on the order of a few bits out of ~128,
  and the class guarantee is a deliberate usability feature (sites that demand a digit). Filed so
  the trade is recorded rather than rediscovered. The uniform alternative is rejection sampling:
  draw all `length` characters from the combined pool, redraw while a selected class is missing.

  The related fixed-order truncation bug at small lengths WAS fixed in the audit pass; this is
  the remaining statistical half.
  **Layman:** Generated passwords contain slightly more digits and symbols than pure chance would give, which costs a little randomness.
  Kind: fix.
  Source: review-code 2026-08-31 lane 3 (verified; NOT a security defect).
  Lanes: data-model.

- 📋 [ROLO-0070] **GUI robustness: silent no-ops, a rebuild inside a drop handler, and unbounded preview rows.**
  Six small verified findings, grouped because they are one pass over the dialog layer.

  - Silent no-ops with no feedback: AddEditDialog._on_save returns on an empty name (blessed by
    INV-6, but the Save button visibly does nothing); ImportPreviewDialog._on_import with nothing
    ticked; ManageCategoriesDialog rename on an empty/unchanged/duplicate name; _add_category
    when add_category returns False. A user trying to merge two categories by renaming gets
    silence.
  - _move_entry_to_category runs _refresh_list() synchronously INSIDE the ::drop handler,
    destroying the CategoryHeaderRow that owns the still-executing DropTarget. GLib.idle_add the
    rebuild so the drop completes first.
  - The create-a-vault path runs create_vault (a 600k-iteration KDF) on the main thread with the
    button still sensitive and no "Creating..." label, so the window freezes with no feedback.
    master-password.md INV-6 requires the thread for UNLOCK only, so this is the inconsistent
    half rather than a breach.
  - _refresh_list would render a category header and its entries TWICE if "" or a duplicate ever
    reached vault["categories"]. Belt-and-braces given ROLO-0064's rename guard.
  - ChangePasswordDialog connects no `activate` handler, so Enter does not submit -- unlike
    RestorePasswordDialog and UnlockDialog, which both do.
  - ImportPreviewDialog builds one Adw.ActionRow per parsed entry synchronously with no cap, so a
    large import file freezes the UI in the constructor.
  **Layman:** Several actions do nothing at all when they fail, with no message — and a couple of screens can misbehave in unusual cases.
  Kind: fix.
  Source: review-code 2026-08-31 lanes 5, 8, 9 (all verified, none fixed in the audit pass).
  Lanes: gui.

- 📋 [ROLO-0071] **A legacy or hand-edited vault can raise KeyError instead of degrading.**
  Direct dict indexing sits beside defensive .get() calls in the same functions, so the code is
  inconsistent about whether a malformed field is survivable:

  - audit_passwords uses f["label"] after .get()-ing `sensitive` and `value` two lines earlier.
  - search_entries uses field["label"] and field["value"] directly -- inside search-as-you-type.
  - AddEditDialog.__init__ uses entry["name"] and field["label"]/["value"] while defensively
    .get()-ing `sensitive`, so a vault missing either key makes Edit appear dead.

  The migrate_vault guard added in the audit pass now rejects a vault with no `entries` dict, but
  it does not validate individual FIELDS, so this population is still reachable. One finding was
  dismissed during the audit as unreachable-in-practice; the AddEditDialog case is the one that
  makes it worth doing anyway, since it presents as a dead button rather than an error.
  **Layman:** An old or hand-edited vault file can make an entry refuse to open, rather than showing what it can.
  Kind: fix.
  Source: review-code 2026-08-31 lanes 3 and 8 (verified, not fixed in the audit pass).
  Lanes: data-model, gui.

- 📋 [ROLO-0072] **Error text puts the full backup path on screen.**
  RestorePasswordDialog._try_unlock catches broadly and passes str(e) to _unlock_fail, so an
  OSError's message -- which carries the full path of the backup file -- is rendered in the
  dialog. Minor local disclosure, on a screen the user opened themselves.

  Fix: map non-InvalidToken failures to a fixed "Could not read that backup file." and keep the
  detail out of the UI. Pairs with the broader `except Exception` narrowing the audit pass began
  but did not finish -- python.md § Spellings asks for named exceptions and several call sites
  still catch Exception.
  **Layman:** If a backup file cannot be read, the message shows exactly where it lives on your disk.
  Kind: security.
  Source: review-code 2026-08-31 lane 9 (verified, not fixed in the audit pass).
  Lanes: gui.

- 📋 [ROLO-0073] **GTK deprecations and an unguarded default display.**
  Two items in do_startup:

  - Gtk.StyleContext.add_provider_for_display has been deprecated since GTK 4.10. The project
    deliberately avoided a comparable deprecation for Gtk.ShortcutsWindow, so this is
    inconsistent rather than merely old. Gtk.StyleProvider on the display is the replacement.
  - Gdk.Display.get_default() can return None, which makes that call a TypeError rather than a
    diagnosable message. This session's verify-delivery run hit exactly that class of failure
    headlessly, so it is not hypothetical.

  Related to ROLO-0049, which covers declaring the GTK 4.12 floor that load_from_string needs;
  switching to load_from_data would remove that floor and could be done in the same pass.
  **Layman:** The startup code uses an older way of loading styles that newer versions warn about, and assumes a screen is always available.
  Kind: chore.
  Source: review-code 2026-08-31 lane 9 (verified, not fixed in the audit pass).
  Lanes: gui.

- 📋 [ROLO-0074] **CI-local.sh invokes ruff differently from ci.yml.**
  CI-local.sh runs `python3 -m ruff check rolodex.py tests/`; ci.yml runs bare
  `ruff check rolodex.py tests/`. The module form only works for a pip-installed ruff, not a
  distro-packaged or standalone-installer one.

  It fails LOUDLY rather than silently, so the gate does not go falsely green -- which is why this
  is low rather than medium. But it makes the local mirror unusable on a machine where CI's own
  invocation would work, and CLAUDE.md's whole argument for CI-local.sh is that the two stay in
  lockstep.
  **Layman:** The local pre-push check runs the linter in a way that only works for one kind of install, unlike the real CI.
  Kind: chore.
  Source: review-code 2026-08-31 lane 10 (verified, not fixed in the audit pass).
  Lanes: tooling.

- 📋 [ROLO-0075] **Three packaging assumptions nobody has verified.**
  Lane 10 raised these as open questions rather than findings, having verified it could not
  settle them from a read. Each is cheap to answer and expensive to be wrong about.

  - rolodex.spec passes ["../rolodex.py"] to Analysis. All three build scripts cd to the repo
    root first, so this resolves only if PyInstaller anchors Analysis script paths to SPECPATH
    rather than CWD. CI is green, so it evidently does -- but the two rules disagree here, and
    os.path.join(SPECPATH, "..", "rolodex.py") is unambiguous under both.
  - build.yml's signing step is `shell: bash` (Git Bash) and calls python3. On the
    windows-latest image, python3 on Git Bash's PATH is the classic WindowsApps store-alias trap.
    It fails safe (the job fails, nothing is attached) but the Windows binary would then be
    silently missing from a release. NOTE: the audit pass moved signing to an ubuntu-latest job,
    which removes the exposure -- confirm and then close this half.
  - packaging/rolodex.spec's ROLODEX_CONSOLE escape hatch appears nowhere else in the tree, so
    the Windows console-diagnostic path has never been exercised from CI.
  **Layman:** Three things in the build setup work today but for reasons nobody has actually confirmed.
  Kind: investigate.
  Source: review-code 2026-08-31 lane 10 (open questions the lane declined to guess at).
  Lanes: packaging.

- 📋 [ROLO-0076] **Publish build provenance alongside the Ed25519 signature.**
  The release carries an Ed25519 signature over the artifact bytes, which meets the spec's stated
  threat model (artifact authenticity). OpenSSF/SLSA would additionally expect
  actions/attest-build-provenance, which binds the artifact to the workflow run, commit and
  builder that produced it.

  Lane 10 explicitly filed this as a coverage note rather than a defect, and it is recorded on
  those terms. It becomes more valuable now that the audit pass split signing into its own job:
  provenance is what would let a user verify WHICH run signed a binary, not merely that the key
  did.
  **Layman:** Releases are signed, but there is no machine-checkable record of which workflow run built them.
  Kind: security.
  Source: review-code 2026-08-31 lane 10 (recorded as a coverage note, not a defect).
  Lanes: packaging.

- 📋 [ROLO-0077] **A registered move-to-category action that nothing can fire.**
  MainWindow registers a `move-to-category` window action with a (ss) parameter wired to
  _on_move_to_category_action. A project-wide search (tests excluded) finds no menu model, no
  set_action_name, no accelerator and no activate_action that fires it -- the shipped right-click
  menu uses plain Gtk.Buttons and make_move_handler instead.

  Not a zombie feature: categories.md INV-11/INV-12 promise the MENU, which exists and works. So
  the promise is kept and this is dead wiring behind it. No tool catches it -- vulture sees the
  connect() and considers the handler used.

  Either route the menu through the action or remove the registration; leaving both is the trap,
  because the next person to touch that menu will reasonably assume the action is the live path.
  **Layman:** There is a leftover menu action wired up in the code that no part of the app can actually trigger.
  Kind: chore.
  Source: review-code 2026-08-31 lane 7 (verified; a maintenance trap, not a defect).
  Lanes: gui.

- 📋 [ROLO-0078] **Two behaviour changes 1.3.1 introduced that no document records.**
  The 1.3.1 move from os.open to mkstemp + os.replace changed two behaviours, neither recorded
  in CHANGELOG.md, vault-format-and-crypto.md or DESIGN.md:

  - A vault path that is a SYMLINK is now REPLACED by a regular file. The old os.open followed
    the link and wrote through it. Anyone keeping contacts.vault as a symlink into a synced
    folder gets a silently different arrangement after their first save.
  - Saving now requires WRITE PERMISSION ON THE DIRECTORY, not just on the vault file. A
    read-only directory holding a writable vault used to work and no longer does.

  Both are narrow, both are defensible, and neither is wrong -- os.replace is the correct
  primitive. They are filed because they are user-visible consequences that shipped unannounced,
  and the second now surfaces as ROLO-0045's "Could Not Save" dialog rather than as silence,
  which makes it likelier someone hits it and asks.

  Separately from the same lane: a user moving from `python3 rolodex.py` to the packaged binary
  gets the create-a-new-vault dialog with their real vault intact but invisible, because the
  frozen build uses GLib.get_user_data_dir()/Rolodex and the source run uses the script
  directory. README.md:49 and CHANGELOG.md:132 document the paths, so this is a missing
  affordance rather than a doc divergence -- offer "import an existing vault" on first frozen
  start with no vault.
  **Layman:** Making saves safer quietly changed how the app behaves for people whose vault is a shortcut, or in a folder they cannot write to.
  Kind: fix.
  Source: review-code 2026-08-31 lane 1 (verified; undocumented consequences of a shipped change).
  Lanes: crypto, docs.
