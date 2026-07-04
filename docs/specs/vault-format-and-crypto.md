# Spec: Vault Format & Cryptography

Retroactive spec for the encryption layer (`derive_key`, `save_vault`, `load_vault`,
`create_vault`, `migrate_vault`).

## Behaviour

### File format

- **INV-1** A vault file is exactly `MAGIC (4 bytes, b"VLT1")` + `salt (16 bytes)` +
  `Fernet token (remaining bytes)`, in that order.
- **INV-2** `load_vault` reads the first 4 bytes and raises `ValueError("Not a valid vault
  file")` if they are not `b"VLT1"`, before attempting any decryption.
- **INV-3** The salt is stored in the clear (unencrypted) in the header; it is not secret.

### Key derivation

- **INV-4** The encryption key is `base64.urlsafe_b64encode(PBKDF2HMAC(SHA256, length=32,
  salt, iterations=600_000).derive(password_utf8))`.
- **INV-5** `ITERATIONS` is 600,000 and is treated as a floor — it may increase (with a
  migration), never decrease.
- **INV-6** The salt passed to `derive_key` is the exact 16 bytes read from (or written to)
  the file header for that vault.

### Encryption / decryption

- **INV-7** Plaintext is `json.dumps(vault_data, ensure_ascii=False)` UTF-8 encoded, encrypted
  with `Fernet(key)` (AES-128-CBC + HMAC-SHA256, authenticated).
- **INV-8** Decrypting with the wrong password raises `cryptography.fernet.InvalidToken`
  (surfaced to the user as "Wrong password."); a corrupted/tampered file also fails
  authentication rather than returning garbage.

### File permissions

- **INV-9** Every vault write uses `os.open(path, O_WRONLY | O_CREAT | O_TRUNC, 0o600)` so the
  file is owner-read/write only, regardless of umask. This applies to `save_vault`; backups
  additionally `os.chmod(..., 0o600)`.
- **INV-10** On write error the file descriptor is closed and the exception re-raised (no
  silent swallow).

### Creation & migration

- **INV-11** `create_vault` generates a fresh 16-byte `os.urandom` salt and writes an empty
  v2 vault `{"version": 2, "categories": [], "entries": {}}`.
- **INV-12** `migrate_vault` is idempotent: it ensures `categories` exists, backfills every
  entry's `category` to `""` if missing, and sets `version = 2`. Running it twice yields the
  same result.
- **INV-13** `migrate_vault` is called after every successful `load_vault` (both unlock and
  restore paths) before the data is used.

## Notes

- There is no password recovery by design (see `SECURITY.md`); the password is never stored.
- Changing the master password rotates the salt (`os.urandom(16)`) and re-encrypts on the next
  `_save` — see `master-password.md`.
- Future KDF upgrade (Argon2id) is roadmap ROLO-0005 and will extend the header + INV-4/-5 with
  a recorded algorithm identifier and a `migrate_vault` upgrade branch.
