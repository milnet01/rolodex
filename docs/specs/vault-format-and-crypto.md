# Spec: Vault Format & Cryptography

Genre: spec
Status: active
Describes: current

Retroactive spec for the encryption layer (`derive_key`, `save_vault`, `load_vault`,
`create_vault`, `migrate_vault`, and the `save_vault_with_key` / `load_vault_with_key` /
`create_vault_with_key` siblings that keep the KDF to once per credential — see INV-17).

An invariant below naming `save_vault`, `load_vault` or `create_vault` describes behaviour
implemented in the corresponding `_with_key` sibling and inherited by the wrapper, which only
delegates. INV-13 is the one place that distinction changes what a caller must do.

INV-20 to INV-26 are forward: ROLO-0005 adds them, and they describe no shipped code until it
ships. So is every clause elsewhere that names `VLT2`, Argon2id or `derive_key_pbkdf2`, or
cites INV-20 to INV-26. They add the `VLT2` format, keyed by Argon2id, and the upgrade of a
`VLT1` vault at unlock. Decisions behind them (user, 2026-10-19): upgrade right after unlock; builds 1.6 and
older failing to open a `VLT2` vault is accepted.

## Behaviour

### File format

- **INV-1** A `VLT1` vault file is exactly `MAGIC (4 bytes, b"VLT1")` + `salt (16 bytes)` +
  `Fernet token (remaining bytes)`, in that order. `VLT2` is INV-20.
- **INV-2** `load_vault_with_key` reads the first 4 bytes and raises `ValueError("Not a valid vault
  file")` if they are not a magic it recognises — `b"VLT1"` and `b"VLT2"` — nor a newer member
  of their family (INV-21). A successor format joins that set rather than replacing the check.
  It then requires the rest of that magic's header — for `VLT1`, exactly
  16 salt bytes and raises `ValueError("Vault file is truncated or corrupt")` if fewer remain —
  also before deriving, because the vault would otherwise fail as `InvalidToken`, which
  INV-8's unlock path renders as "Wrong password." Reporting
  a truncated file as a forgotten password is the worst available error for an app with no
  recovery path.
- **INV-3** The salt is stored in the clear (unencrypted) in the header; it is not secret.

### Key derivation

- **INV-4** A `VLT1` vault's encryption key is `base64.urlsafe_b64encode(PBKDF2HMAC(SHA256, length=32,
  salt, iterations=600_000).derive(password_utf8))`. `derive_key_pbkdf2` computes it, and only
  the `VLT1` reader calls it. A `VLT2` vault's key is INV-22.
- **INV-5** `ITERATIONS` is 600,000 and is fixed: `VLT1` implies it, because that header has no
  field to record a count, and an existing `VLT1` vault re-read at another count fails as
  `InvalidToken`, indistinguishable from a wrong password. Nothing writes `VLT1` any more
  (INV-24), so stronger key derivation comes from `VLT2`'s recorded parameters (INV-22), never
  from changing this constant.
- **INV-6** The salt passed to the key derivation is the exact 16 bytes read from (or written
  to) the file header for that vault, in either format (INV-1, INV-20).

### Encryption / decryption

- **INV-7** Plaintext is `json.dumps(vault_data, ensure_ascii=False)` UTF-8 encoded, encrypted
  with `Fernet(key)` (AES-128-CBC + HMAC-SHA256, authenticated).
- **INV-8** Decrypting with the wrong password raises `cryptography.fernet.InvalidToken`. The
  unlock path surfaces this as "Wrong password."; the restore path surfaces "Wrong password for
  this backup." A corrupted/tampered file also fails authentication rather than returning garbage.

### File permissions

- **INV-9** Every secret write ends owner-only (`0o600`), regardless of umask, **and so does an
  overwrite**. `save_vault`, the plaintext export and the backup all go through
  `write_private_file`, which stages the bytes in a `tempfile.mkstemp` temp — created `0600` — in
  the destination's own directory and `os.replace`s it into place, so the replacing inode's `0600`
  carries onto the destination whatever mode the previous file had. The backup does **not**
  `shutil.copy2` then `chmod`: `copyfile` creates the destination through `open(dst, 'wb')` and
  writes the whole payload before `copystat` narrows it, and it truncates an existing backup
  first, so an interrupted backup destroyed the previous good one.
  (Before 1.3.1 the write was `os.open(path, O_WRONLY | O_CREAT | O_TRUNC, 0o600)`, under which an
  overwrite *kept* the existing file's permissions. That is no longer true; see
  `import-export-backup.md` INV-15.)
- **INV-10** A write error propagates to the caller rather than being silently swallowed, and
  the *original* error surfaces (not a masking one). `write_private_file` writes the temp inside a
  `with os.fdopen(fd, "wb")` block, so the fd is closed exactly once on every path; on any failure
  it unlinks the temp — suppressing only the unlink's own `OSError` — and re-raises the original
  exception (regression-tested in `tests/test_vault.py`).

### Creation & migration

- **INV-11** `create_vault` generates a fresh 16-byte `os.urandom` salt and writes an empty
  v2 vault, in the `VLT2` format with the current parameters, `{"version": 2, "categories": [], "entries": {}}`. It raises `FileExistsError`,
  writing nothing, when a file already exists at the path (ROLO-0044).
- **INV-12** `migrate_vault` refuses before it migrates. A vault that is not a dict, or whose
  `entries` is not a dict, raises `ValueError("Vault contents are not a valid vault")`. A vault
  whose `version` is an int greater than 2 raises a `ValueError` naming that version and saying
  to upgrade Rolodex — **before** any stamp is written, because migration is one-way and a v3
  vault relabelled v2 cannot be recovered. Otherwise it is idempotent: it ensures `categories`
  exists, backfills every entry's `category` to `""` if missing, and sets `version = 2`. Running
  it twice yields the same result.
- **INV-13** `migrate_vault` is called after every successful load, before the data is used — on
  the unlock path, on the restore path, and on a freshly created vault. The invariant is over
  every load path, not over `load_vault`: the application calls the `_with_key` siblings and
  never the plain wrappers, so a new caller of those siblings owes this call too.

- **INV-17** The KDF runs once per credential, not once per save. `save_vault_with_key` takes an
  already-derived key and does no derivation. `load_vault_with_key` and `create_vault_with_key`
  cannot take one, for two different reasons: a vault's salt lives inside the file being opened,
  and a new vault's salt does not exist until `create_vault_with_key` mints it. Both therefore
  take a password, derive once, and **return** the key alongside the data:
  `(vault, salt, key)` and `(vault_data, salt, key)`. The pair `load_vault_with_key` returns
  is always a current-format pair, which may not be the file's own (INV-24); for a stale file
  that costs a second derivation, once. `save_vault`, `load_vault` and
  `create_vault` remain, keeping their original signatures and return shapes, so no existing
  caller changed. An
  open session holds the key for its current `(password, salt)` pair and re-derives only where
  the salt rotates — a master-password change and a backup restore. Two consequences bind any
  new caller. A key must only ever be written alongside the salt it was derived from, because
  the header salt is what a later unlock derives against and a mismatched pair yields a vault
  no password opens (INV-6 is the same rule stated from the reading side). And the derived key
  is the master password in another form: it lives only in memory, is never written, and is
  cleared wherever the password is.

- **INV-16** A secret write is **atomic**: the bytes are written to a temp in the destination's
  own directory, `flush`ed and `os.fsync`ed, then `os.replace`d into place. An interrupted write —
  a crash, a full disk, a power cut — therefore leaves the previous file byte-for-byte intact
  rather than truncating it. The rename is not itself followed by a directory `fsync`, so a power
  cut may lose the *new* contents; it can never corrupt the old.
  Removing the temp is a handler, so it covers what a handler can reach: any exception, and a
  signal that raises (`KeyboardInterrupt`, `SystemExit`) — hence `except BaseException`, since
  neither of those is an `Exception` subclass. A power cut or `SIGKILL` runs no handler and can
  strand a `.rolodex-*.tmp` holding the complete ciphertext; it is `0600` (INV-9) so it is not
  world-readable, but nothing removes it later. Regression-tested in `tests/test_vault.py`.
  A destination that is a symlink is written **through**: the temp is staged beside the file
  the link resolves to, and the link survives (ROLO-0078). The write needs permission to
  create a file in that directory, not only to write the vault file itself.

### Argon2id and the `VLT2` format (ROLO-0005)

- **INV-20** A `VLT2` vault file is exactly `b"VLT2"` + `memory_cost` + `iterations` + `lanes` +
  `salt (16 bytes)` + `Fernet token (remaining bytes)`, in that order. Each of the three
  parameters is a 4-byte big-endian unsigned integer; `memory_cost` is in KiB. The 32-byte
  header is in the clear, and its parameters are no more secret than the salt (INV-3). Broken
  by: a writer that orders or sizes the fields differently, which a reader built to this
  layout then decrypts under the wrong salt or parameters.
- **INV-21** Every vault magic is `b"VLT"` followed by one ASCII digit. A magic of that shape
  whose digit is above the newest this build writes raises `VaultTooNewError`, a `ValueError`
  subclass, with the message "This vault was made by a newer version of Rolodex. Update Rolodex
  to open it." Any other unrecognised 4 bytes still raise "Not a valid vault file" (INV-2).
  `VaultTooNewError` is not a damaged file, so the unlock screen shows it without the restore
  and start-fresh options it offers for an unreadable vault (`master-password.md` INV-7a
  excepts it), and the restore dialog and
  `adopt_vault_file` report it the same way. Builds 1.6 and older predate this rule and call a
  `VLT2` file "Not a valid vault file"; that is accepted. Broken by: a `b"VLT9"` file reported
  as not a vault, or one that opens the unlock screen's recovery options.
- **INV-22** A `VLT2` vault's key is `base64.urlsafe_b64encode(Argon2id(salt, length=32,
  iterations, lanes, memory_cost).derive(password_utf8))`, using the parameters its own header
  records. `derive_key(password, salt)` derives with the current parameters:
  `ARGON2_MEMORY_KIB = 262_144` (256 MiB), `ARGON2_ITERATIONS = 3` and `ARGON2_LANES = 4`.
  Those are a floor. They may rise and never fall, and raising them is not a format change,
  because every `VLT2` header records the values it was written with. Argon2id comes from the
  `cryptography` package already required, so this adds no dependency. Broken by: a reader
  that derives a `VLT2` key from the constants instead of the header, which opens no vault
  written before a raise.
- **INV-23** Before deriving, the `VLT2` reader checks the header. Fewer than the 28 bytes
  after the magic raises "Vault file is truncated or corrupt", as INV-2 does for `VLT1`. So
  does `lanes` or `iterations` of zero, or `memory_cost` below `8 × lanes`, which Argon2id
  itself rejects. A value above its cap raises `VaultTooNewError`: `memory_cost` above
  `ARGON2_MAX_MEMORY_KIB = 4_194_304` (4 GiB), `iterations` above `ARGON2_MAX_ITERATIONS = 64`,
  or `lanes` above `ARGON2_MAX_LANES = 64`. So a damaged header can never make an unlock
  allocate unbounded memory or run without end, and a later build that raises past a cap is
  reported as newer rather than as corrupt. Broken by: a header with `memory_cost` of
  `0xFFFFFFFF` reaching `Argon2id`.
- **INV-24** `save_vault_with_key` always writes `VLT2` with the current parameters. So the key
  a session holds must always belong to those parameters, and `load_vault_with_key` makes it
  so. When the file is current — `VLT2` with exactly the current parameters — it returns the
  file's salt and key. Otherwise — `VLT1`, or `VLT2` with other parameters — it derives the
  file's own key to decrypt, then mints a fresh `os.urandom(16)` salt and derives a current
  key, and returns that pair. *Other* includes higher parameters from a newer build; this build
  then writes its own, which are never below its floor, and the newer build raises them again
  at its next unlock. Broken by: a session that unlocked a `VLT1` vault and whose first save
  writes a vault the same password does not open.
- **INV-25** Unlocking upgrades the file. `unlock_vault(password, path)` in the pure layer
  calls `_read_vault(password, path)`, the reader `load_vault_with_key` wraps. It returns
  `(vault, salt, key, current)`, where `current` is true exactly when INV-24 finds the file
  current; `load_vault_with_key` returns the first three. When `current` is false,
  `unlock_vault` writes the decrypted vault back with `save_vault_with_key` and the returned
  pair before returning `(vault, salt, key)`.
  It writes the vault as it decrypted, before `migrate_vault`. The unlock dialog's background
  thread calls it while holding `VaultLock` (INV-18), so the window's fingerprint (INV-19) is
  taken after the rewrite. An `OSError` from that write is not an unlock failure: the vault
  opens, the file keeps its old format, and the session's first save writes `VLT2`. The restore
  path calls `load_vault_with_key` and never `unlock_vault`, so a backup file it reads is never
  rewritten. Broken by: a `VLT1` vault whose first bytes are still `VLT1` after a successful
  unlock into a writable folder.
- **INV-26** `adopt_vault_file` accepts any header that passes INV-2, INV-21 and INV-23, and
  copies the bytes verbatim. It does not upgrade; the unlock that follows does. Broken by: a
  `VLT2` backup refused at the unlock screen's restore option.

### Concurrent writers

- **INV-18** A session holds an exclusive, non-blocking advisory lock (`VaultLock`) on a
  `<real vault path>.lock` sidecar from the moment it commits to unlocking or creating until
  its window closes or locks. A second session asking for the same lock gets `VaultBusyError`,
  and the unlock screen shows its message instead of unlocking. The sidecar is never deleted.
  A lock file that cannot be created (a read-only directory) does not block unlocking.
- **INV-19** Every write from an open session goes through `MainWindow._write_vault`, which
  compares `vault_fingerprint` — `(inode, size, mtime_ns)` of the real file — with the value
  recorded at unlock and after the session's own last write. On a mismatch it raises
  `VaultChangedError` and writes nothing. A save then asks whether to reload from disk or
  overwrite; the password change and the restore abort with a message. This is what covers a
  vault on a network or synced folder, where INV-18's lock does not reach (ROLO-0044).

## Notes

- There is no password recovery by design (see `SECURITY.md`); the password is never stored.
- Changing the master password rotates the salt (`os.urandom(16)`) and re-encrypts immediately.
  Its handler writes through `_write_vault` directly rather than through `_save`, so the
  write is ordered before the new credentials are adopted and a failed write leaves the session
  on the old pair — see `master-password.md`.
- The KDF upgrade (ROLO-0005) lives in the header, not in `migrate_vault`, which sees only the
  decrypted dict. INV-24 and INV-25 carry it.
- Measured 2026-10-19 on the maintainer's desktop, one derivation each: Argon2id at the INV-22
  parameters took 0.215 s, and PBKDF2 at 600,000 iterations took 0.066 s. Refute by timing
  `derive_key` and `derive_key_pbkdf2` on the same machine.

## 12. Cold-eyes loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Q4 | Outcome |
|------|------|-------|----|----|----|----|---------|
| 1 | 2026-09-02 | 3, cold — genre pinned `spec`; trigger ROLO-0081 (INV-17 added by ROLO-0043, INV-16 narrowed by ROLO-0060) | 3 | 1 | 1 | 0 | **Five verified, five fixed; one dismissed, one out of scope.** **Three defects were found independently by all three lanes**, the strongest agreement available here. **The worst landed on INV-17 itself — the text that armed this gate.** It said "the `_with_key` siblings take an already-derived key and do no derivation", and that is false for two of the three: `load_vault_with_key` and `create_vault_with_key` both take a *password*, derive once internally, and *return* the key. Only `save_vault_with_key` takes one. An implementer giving all three a uniform `(..., key, salt, path)` signature would find `load_vault_with_key` unbuildable, because the salt lives inside the file being opened and no key can exist before the read. The opening paragraph repeated the error and was corrected with it. **INV-12 stated the version stamp unconditionally**, omitting both of `migrate_vault`'s refusals — a non-dict vault, and a `version > 2` vault that raises *before* any stamp. An implementer building from it writes the unconditional stamp, which relabels a v3 vault as v2; migration is one-way, so the lie persists on the next save and nothing recovers it. That guard is load-bearing for the format bump ROLO-0005 plans. **INV-13 named a function nothing calls**: `load_vault` and `create_vault` have zero call sites outside their own bodies — they exist for the tests — while the app calls the `_with_key` siblings. Satisfying INV-13 literally would leave every live path unmigrated. The one Q3 was the **short-salt guard**, specified nowhere: INV-2 pins only the magic check and INV-6 says "the exact 16 bytes" without forcing a length test, so an implementer lets a short read through to `derive_key`, which accepts it silently, and the truncated vault then surfaces as `InvalidToken` — which INV-8's unlock path renders as "Wrong password." Telling a user they mistyped their password for a corrupt file is the worst available error for an app with no recovery path. The Q2 was internal: INV-5 reserved the KDF-upgrade mechanism to "the notes", while the Notes described a `migrate_vault` upgrade branch — and `migrate_vault` only ever sees the decrypted dict, so it cannot read a header or select a KDF. INV-5 now governs and the Notes defer to it. **Collateral, both fixed:** `CLAUDE.md` carried the same false `load_vault` claim as INV-13, and `security-standards.md`'s citation of "the notes" went stale against this run's own INV-5 fix. **Out of scope:** one lane found `master-password.md` INV-8/INV-11 stating adopt-then-save where the code and this document both require save-then-adopt; that is a neighbouring document's defect and was filed, not carried here. **Dismissed as immaterial:** one lane noted "cleared" is undefined for an immutable `bytes` (rebind vs zeroise); nothing else binds to the distinction. Four lane open questions resolved clean and are not counted — INV-8's two user-facing strings exist verbatim, `self._key` is cleared alongside `self.password` at both lock sites, and the password-change handler does write through `save_vault_with_key` before adopting. |
| 2 | 2026-09-02 | 3, cold — identical brief, packet rebuilt whole from disk | 2 | 2 | 0 | 0 | **Four verified, four fixed; one rediscovered and already filed. Cap reached (2 for a spec) — the run files its tail and exits.** **Three of the four landed on text loop 1 wrote, which is a high share: this is a violent cap, the run settling its own prose rather than the document.** The gate does not re-arm on it — route the spec to implementation, which is the better third reviewer. Loop 1's INV-17 repair carried a rationale that is false for half of what it explains: "a vault's salt lives inside the file, so no key exists before the read" is true of `load_vault_with_key` and not of `create_vault_with_key`, which performs no read and mints its salt with `os.urandom`. One lane filed it and a second raised it as an open question. Left as written, a builder adding a re-wrap or "save vault as" path reads *cannot* as an architectural bar and re-derives — 600k PBKDF2 rounds back on the GTK main thread, the exact cost INV-17 exists to remove. Both functions now carry their own reason. Loop 1 also reworded the opening paragraph to say the siblings "avoid re-deriving" the key, which reads as *do not derive* and so contradicted the INV-17 it points at. And loop 1's INV-13 fix drew a wrapper-versus-sibling distinction the rest of the document does not keep: INV-2, INV-9 and INV-11 each name a plain wrapper for behaviour its `_with_key` sibling implements — INV-9's `write_private_file` call site is in `save_vault_with_key`, not `save_vault`. Two lanes found it. Fixed once, by stating the convention in the opening paragraph, rather than by editing three invariants. **The one pre-existing defect was INV-2 against INV-5**: INV-2 rejected any magic that is not `VLT1`, absolutely, so a loader built to it would reject the very files INV-5's new-magic format mandates. INV-2 now rejects a magic *it does not recognise*, and a successor joins that set. **Surfaced, not auto-applied:** the successor magic's shape and what a build predating it should do on meeting one are design decisions, recorded on ROLO-0005 rather than invented here. **Already filed, not re-counted:** two lanes rediscovered the `master-password.md` ordering conflict — ROLO-0083, filed in loop 1. That was the orchestrator's omission: a surfaced finding should have entered this loop's brief as already-known, and did not. **A procedural defect in this run, disclosed by two lanes unprompted:** a project-wide `workspace_search` returns the loop-log row from the *unscrubbed original*, so the scrubbed copy does not withhold review history from a lane that searches the tree. One lane weighted its own finding down accordingly. |
| 3 | 2026-10-19 | 2, cold — genre pinned `spec`; new run, trigger 9927cd6 (ROLO-0005: INV-20 to INV-26 and the amended INV-1, 2, 4, 5, 6, 11, 17). Lane A headless through `neutral-lane`, lane B in-session through the `Agent` tool; both held every question | 2 | 2 | 1 | 0 | **Five findings: three verified and three fixed, two dismissed. Loop 1 of this run.** **Both lanes found the Q3:** INV-25's `unlock_vault` rewrote the file "when the file was not current", but `load_vault_with_key` returns `(vault, salt, key)` and carries no such signal, so each builder would invent one — a fourth return value breaks the restore dialog's three-way unpack. INV-25 now names `_read_vault`, returning `(vault, salt, key, current)`, which `load_vault_with_key` wraps. **One Q1:** the opening paragraph called only INV-20 to INV-26 forward, while INV-2, INV-4, INV-5, INV-11 and INV-17 also describe unshipped behaviour; it now states the search that finds every forward clause, and INV-6's "either format" cites INV-1 and INV-20 so the search reaches it. **One Q2, raised as a lane's open question and verified here:** `import-export-backup.md` INV-13 says a restored vault becomes live "with its own salt", which INV-24 makes false for a stale backup; a builder honouring it would pair the backup's salt with a current key, and no password opens that vault. Corrected there by deleting the clause. **Dismissed:** that INV-2 named `derive_key` as accepting a short salt, which INV-4 and INV-22 make false; it changes nothing built, so the clause was deleted under the false-sentence carve-out and is not counted as verified. And `versioning-standards.md` calling a downgrade-breaking KDF change MAJOR, against the user's 2026-10-19 ruling that ROLO-0005 ships in 1.7.0; the spec names no release. Three lane open questions resolved clean: the stale `CLAUDE.md`, `SECURITY.md`, `DESIGN.md` and security-standards descriptions record shipped code and move when ROLO-0005 ships; the Notes timing is a dated measurement; INV-23's newer-not-corrupt choice is stated deliberately. |
| 4 | 2026-10-19 | 2, cold — identical brief, packet rebuilt whole from disk; both lanes headless through `neutral-lane`, both held every question | 0 | 3 | 0 | 0 | **Three findings: two verified, one fixed and one filed; one dismissed. Cap reached (2 for a spec): loop 2 of this run, and the run exits.** **Both lanes found the Q2:** INV-21 hides the recovery options for `VaultTooNewError`, a `ValueError` subclass, while `master-password.md` INV-7a reveals them for every `ValueError`; a builder following INV-7a keeps one handler and shows them. INV-7a now excepts it, pointing at INV-21. **One lane found the rewrite at unlock skipping INV-19's fingerprint check**, so a synced change landing between the read and the rewrite is overwritten. Verified, and it predates this change: `MainWindow.__init__` takes the first fingerprint after the read, so today the session's first save loses that change the same way. Filed as ROLO-0098 rather than invented here. **Dismissed:** two lanes again set `versioning-standards.md`'s MAJOR rule against "builds 1.6 and older", answered by the user's 2026-10-19 ruling that ROLO-0005 ships in 1.7.0. Five open questions resolved clean, two by measurement: `Argon2id` accepts its arguments positionally, and `memory_cost=262144` grew peak memory by 263,100 KiB, so the unit is KiB. **Calm cap:** one of the three findings is anchored on a sentence this run's previous loop reworded, and its substance predates the run. Four of this run's five verified findings are anchored inside the span 9927cd6 armed; the fifth sits in `import-export-backup.md`, made false by INV-24. This loop's one fix was read by no lane. |
