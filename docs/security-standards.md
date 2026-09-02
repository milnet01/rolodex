# Security Standards

Engineering rules for anyone changing Rolodex. These are stricter than ordinary coding style
because this app guards secrets. `SECURITY.md` is the outward-facing policy; this file is the
internal checklist. When the two overlap, they must agree.

Cold-review history for this document is kept in `review-2026-09-02-security-standards.md`.

## Non-negotiables

1. **Secrets never touch disk unencrypted — except where the user explicitly asks.** The only
   plaintext-writing path is the *Export* feature, which is gated behind a confirmation dialog.
   Do not add logging, temp files, crash dumps, or caches that contain field values or the
   master password.

2. **Every secret-bearing file (vault, export, backup) is created `0600`.** Route every such
   write through `write_private_file()`, which stages via `tempfile.mkstemp` (0600 from
   creation), `fsync`s, and `os.replace`s into position — so the file is never briefly
   world-readable and an interrupted write cannot truncate the previous good copy
   (`vault-format-and-crypto.md` INV-9 / INV-16). Never `open(path, "w")` for vault, export or
   backup data — that respects the umask and can be world-readable. **Never `shutil.copy2` then
   `chmod` either**: `copyfile` creates the destination through `open(dst, 'wb')` and writes the
   whole payload before `copystat` narrows it. (The non-secret `.rolodex.conf` file is exempt
   from the 0600 rule — it holds no secrets — though it is written atomically for durability.)

   This clause described `os.open(..., O_TRUNC, 0o600)` until 1.3.1 made vault writes atomic.
   That `O_TRUNC` form is no longer used anywhere and following it would silently undo INV-16.
   (The `O_EXCL` form in rule 3 is a different case and is still correct — see there.)

3. **A secret-bearing file that is a signing key is covered too.** `scripts/gen-signing-key.py`
   writes the release private key with `os.open(..., O_CREAT | O_EXCL, 0o600)`, and CI never
   writes it to disk at all — `.github/workflows/build.yml` reads it from the environment. Do
   not add a path that stages it in a workspace.

   **What "covered" means here: the `0600`-from-creation requirement applies; rule 2's routing
   through `write_private_file()` does not, and must not be applied to a key.** That helper ends
   in `os.replace`, which overwrites an existing file silently; `O_EXCL` fails on one instead,
   and `gen-signing-key.py` refuses up front as well. Replacing a release private key without
   the operator noticing is unrecoverable — every already-published signature stops verifying.

4. **Don't weaken the KDF.** `ITERATIONS = 600_000` PBKDF2-HMAC-SHA256 is the floor. It may be
   raised (with a migration path), never lowered. The salt stays 16 random bytes, unique per
   vault, generated with `os.urandom`.

5. **Never persist the master password.** It lives only as a local variable / `self.password`
   for the session. No writing it to config, no environment variables, no clipboard.

6. **Preserve authenticated encryption.** Fernet gives confidentiality + integrity. Do not
   swap in a raw cipher, disable the HMAC, or hand-roll crypto. Use the `cryptography` library.

## Review checklist for security-relevant changes

Before merging anything that touches crypto, file I/O, import/export, or clipboard:

- [ ] No new code path writes a secret to disk in plaintext (outside the gated export).
- [ ] Every new or overwritten secret-bearing write routes through `write_private_file()` —
      `0600` from creation, `fsync` + `os.replace`. A trailing `chmod` does not satisfy this,
      because the file is readable until it runs. A signing key is the one exception:
      `O_CREAT | O_EXCL`, per non-negotiable 3.
- [ ] The master password is not logged, cached, or persisted.
- [ ] KDF iterations and salt handling are unchanged or strengthened, with migration if the
      on-disk format changed.
- [ ] Imported/parsed input can't cause a crash that leaks state; parse errors surface as a
      dialog, not an unhandled traceback.
- [ ] Subprocess calls pass their arguments as a list and never through a shell — no
      `shell=True` anywhere, not only in the clipboard helpers. Secret data goes in over stdin,
      never as an argument, and anything whose output is awaited carries a timeout.
- [ ] No absolute personal paths are introduced (see file-naming standard).

## Input handling

- Treat imported text files as untrusted. `parse_text_file` must not execute or `eval`
  anything from the file; it only splits and regex-matches.
- Escape user-controlled strings before putting them in GTK markup
  (`GLib.markup_escape_text`) — already done for titles; keep it up for any new markup sink.

## Dependencies & supply chain

- Keep `cryptography` reasonably current; it is the one security-critical dependency. When
  bumping it, skim its changelog for anything affecting Fernet/PBKDF2. `requirements.txt` pins a
  floor of `>=44.0.0` because older releases carry known CVEs — never drop below it; prefer the
  latest (see `dependency-management-standards.md`).
- Adding any new dependency that handles secrets or does crypto requires explicit review — the
  default answer is "use the standard library or `cryptography`."

## Reporting

Security issues are reported privately per `SECURITY.md`, never in public issues or commit
messages.
