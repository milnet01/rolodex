# Spec: Vault Format & Cryptography

Retroactive spec for the encryption layer (`derive_key`, `save_vault`, `load_vault`,
`create_vault`, `migrate_vault`, and the `save_vault_with_key` / `load_vault_with_key` /
`create_vault_with_key` siblings that keep the KDF to once per credential — see INV-17).

An invariant below naming `save_vault`, `load_vault` or `create_vault` describes behaviour
implemented in the corresponding `_with_key` sibling and inherited by the wrapper, which only
delegates. INV-13 is the one place that distinction changes what a caller must do.

## Behaviour

### File format

- **INV-1** A vault file is exactly `MAGIC (4 bytes, b"VLT1")` + `salt (16 bytes)` +
  `Fernet token (remaining bytes)`, in that order.
- **INV-2** `load_vault` reads the first 4 bytes and raises `ValueError("Not a valid vault
  file")` if they are not a magic it recognises — today `b"VLT1"` alone. A successor format joins
  that set rather than replacing the check, so INV-5's new magic does not make this invariant
  reject the files it mandates. It then requires exactly
  16 salt bytes and raises `ValueError("Vault file is truncated or corrupt")` if fewer remain —
  also before deriving, because `derive_key` accepts a short salt silently and the vault would
  then fail as `InvalidToken`, which INV-8's unlock path renders as "Wrong password." Reporting
  a truncated file as a forgotten password is the worst available error for an app with no
  recovery path.
- **INV-3** The salt is stored in the clear (unencrypted) in the header; it is not secret.

### Key derivation

- **INV-4** The encryption key is `base64.urlsafe_b64encode(PBKDF2HMAC(SHA256, length=32,
  salt, iterations=600_000).derive(password_utf8))`.
- **INV-5** `ITERATIONS` is 600,000 and is treated as a floor — it may increase, never decrease.
  An increase is a **format change**, because INV-1's header records no iteration count: an
  existing vault re-read at a higher count fails as `InvalidToken`, indistinguishable from a
  wrong password. It therefore needs a **new magic**: `VLT1` implies 600,000 — there is no field
  in that header to read it from — and the new format records its count so later raises can read
  it. ROLO-0005 is the item that would introduce it, and it must settle two things this document
  deliberately leaves open: the successor magic's shape, and what a build predating it does on
  meeting one — a recognisable family would let today's loader raise INV-12's "upgrade Rolodex"
  error instead of "Not a valid vault file".
- **INV-6** The salt passed to `derive_key` is the exact 16 bytes read from (or written to)
  the file header for that vault.

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
  v2 vault `{"version": 2, "categories": [], "entries": {}}`.
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
  `(vault, salt, key)` and `(vault_data, salt, key)`. `save_vault`, `load_vault` and
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

## Notes

- There is no password recovery by design (see `SECURITY.md`); the password is never stored.
- Changing the master password rotates the salt (`os.urandom(16)`) and re-encrypts immediately.
  Its handler writes through `save_vault_with_key` directly rather than through `_save`, so the
  write is ordered before the new credentials are adopted and a failed write leaves the session
  on the old pair — see `master-password.md`.
- Future KDF upgrade (Argon2id) is roadmap ROLO-0005, and INV-5 governs the mechanism: a new
  magic whose cleartext header records the algorithm and its parameters, which the loader reads
  to choose a KDF. `migrate_vault` cannot do that — it sees only the decrypted dict, never the
  header — so its part is the post-decryption re-wrap alone.

## 12. Cold-eyes loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Q4 | Outcome |
|------|------|-------|----|----|----|----|---------|
| 1 | 2026-09-02 | 3, cold — genre pinned `spec`; trigger ROLO-0081 (INV-17 added by ROLO-0043, INV-16 narrowed by ROLO-0060) | 3 | 1 | 1 | 0 | **Five verified, five fixed; one dismissed, one out of scope.** **Three defects were found independently by all three lanes**, the strongest agreement available here. **The worst landed on INV-17 itself — the text that armed this gate.** It said "the `_with_key` siblings take an already-derived key and do no derivation", and that is false for two of the three: `load_vault_with_key` and `create_vault_with_key` both take a *password*, derive once internally, and *return* the key. Only `save_vault_with_key` takes one. An implementer giving all three a uniform `(..., key, salt, path)` signature would find `load_vault_with_key` unbuildable, because the salt lives inside the file being opened and no key can exist before the read. The opening paragraph repeated the error and was corrected with it. **INV-12 stated the version stamp unconditionally**, omitting both of `migrate_vault`'s refusals — a non-dict vault, and a `version > 2` vault that raises *before* any stamp. An implementer building from it writes the unconditional stamp, which relabels a v3 vault as v2; migration is one-way, so the lie persists on the next save and nothing recovers it. That guard is load-bearing for the format bump ROLO-0005 plans. **INV-13 named a function nothing calls**: `load_vault` and `create_vault` have zero call sites outside their own bodies — they exist for the tests — while the app calls the `_with_key` siblings. Satisfying INV-13 literally would leave every live path unmigrated. The one Q3 was the **short-salt guard**, specified nowhere: INV-2 pins only the magic check and INV-6 says "the exact 16 bytes" without forcing a length test, so an implementer lets a short read through to `derive_key`, which accepts it silently, and the truncated vault then surfaces as `InvalidToken` — which INV-8's unlock path renders as "Wrong password." Telling a user they mistyped their password for a corrupt file is the worst available error for an app with no recovery path. The Q2 was internal: INV-5 reserved the KDF-upgrade mechanism to "the notes", while the Notes described a `migrate_vault` upgrade branch — and `migrate_vault` only ever sees the decrypted dict, so it cannot read a header or select a KDF. INV-5 now governs and the Notes defer to it. **Collateral, both fixed:** `CLAUDE.md` carried the same false `load_vault` claim as INV-13, and `security-standards.md`'s citation of "the notes" went stale against this run's own INV-5 fix. **Out of scope:** one lane found `master-password.md` INV-8/INV-11 stating adopt-then-save where the code and this document both require save-then-adopt; that is a neighbouring document's defect and was filed, not carried here. **Dismissed as immaterial:** one lane noted "cleared" is undefined for an immutable `bytes` (rebind vs zeroise); nothing else binds to the distinction. Four lane open questions resolved clean and are not counted — INV-8's two user-facing strings exist verbatim, `self._key` is cleared alongside `self.password` at both lock sites, and the password-change handler does write through `save_vault_with_key` before adopting. |
| 2 | 2026-09-02 | 3, cold — identical brief, packet rebuilt whole from disk | 2 | 2 | 0 | 0 | **Four verified, four fixed; one rediscovered and already filed. Cap reached (2 for a spec) — the run files its tail and exits.** **Three of the four landed on text loop 1 wrote, which is a high share: this is a violent cap, the run settling its own prose rather than the document.** The gate does not re-arm on it — route the spec to implementation, which is the better third reviewer. Loop 1's INV-17 repair carried a rationale that is false for half of what it explains: "a vault's salt lives inside the file, so no key exists before the read" is true of `load_vault_with_key` and not of `create_vault_with_key`, which performs no read and mints its salt with `os.urandom`. One lane filed it and a second raised it as an open question. Left as written, a builder adding a re-wrap or "save vault as" path reads *cannot* as an architectural bar and re-derives — 600k PBKDF2 rounds back on the GTK main thread, the exact cost INV-17 exists to remove. Both functions now carry their own reason. Loop 1 also reworded the opening paragraph to say the siblings "avoid re-deriving" the key, which reads as *do not derive* and so contradicted the INV-17 it points at. And loop 1's INV-13 fix drew a wrapper-versus-sibling distinction the rest of the document does not keep: INV-2, INV-9 and INV-11 each name a plain wrapper for behaviour its `_with_key` sibling implements — INV-9's `write_private_file` call site is in `save_vault_with_key`, not `save_vault`. Two lanes found it. Fixed once, by stating the convention in the opening paragraph, rather than by editing three invariants. **The one pre-existing defect was INV-2 against INV-5**: INV-2 rejected any magic that is not `VLT1`, absolutely, so a loader built to it would reject the very files INV-5's new-magic format mandates. INV-2 now rejects a magic *it does not recognise*, and a successor joins that set. **Surfaced, not auto-applied:** the successor magic's shape and what a build predating it should do on meeting one are design decisions, recorded on ROLO-0005 rather than invented here. **Already filed, not re-counted:** two lanes rediscovered the `master-password.md` ordering conflict — ROLO-0083, filed in loop 1. That was the orchestrator's omission: a surfaced finding should have entered this loop's brief as already-known, and did not. **A procedural defect in this run, disclosed by two lanes unprompted:** a project-wide `workspace_search` returns the loop-log row from the *unscrubbed original*, so the scrubbed copy does not withhold review history from a lane that searches the tree. One lane weighted its own finding down accordingly. |
