# Security Standards

Genre: standard
Status: active

Engineering rules for anyone changing Rolodex. These are stricter than ordinary coding style
because this app guards secrets. `SECURITY.md` is the outward-facing policy; this file is the
internal checklist. When the two overlap, they must agree.

Cold-review history for this document is kept in `review-2026-09-02-security-standards.md`.

## Non-negotiables

1. **Secrets never touch disk unencrypted — except where the user explicitly asks.** The only
   plaintext-writing path is the *Export* feature, which is gated behind a confirmation dialog.
   Do not add logging, temp files, crash dumps, or caches that contain field values or the
   master password. The `0600` temp that `write_private_file()` stages is not such a temp file —
   rule 2 requires it, and `os.replace` moves it into place within the same call.

2. **Every secret-bearing file (vault, export, backup) is created — and overwritten — `0600`.**
   Route every such
   write through `write_private_file()`, which stages via `tempfile.mkstemp` (0600 from
   creation), `fsync`s, and `os.replace`s into position — so the file is never briefly
   world-readable and an interrupted write cannot truncate the previous good copy
   (`vault-format-and-crypto.md` INV-9 / INV-16). Never `open(path, "w")` for vault, export or
   backup data — that respects the umask and can be world-readable. **Never `shutil.copy2` then
   `chmod` either**: `copyfile` creates the destination through `open(dst, 'wb')` and writes the
   whole payload before `copystat` narrows it. (The non-secret `.rolodex.conf` file is exempt
   from the 0600 rule — it holds no secrets — though it is written atomically for durability.)

   This clause described `os.open(..., O_TRUNC, 0o600)` until 1.3.1 made vault writes atomic.
   That `O_TRUNC` form is no longer used anywhere. Following it would undo INV-16's atomicity
   **and** INV-9's overwrite guarantee: `os.open`'s mode argument applies only when the file is
   created, so re-opening an existing `0644` file with `0o600` leaves it `0644`. Atomicity is
   not the only thing that form cost. (The `O_EXCL` form in rule 3 is a different case and is
   still correct — see there.)

3. **A secret-bearing file that is a signing key is covered too.** `scripts/gen-signing-key.py`
   writes the release private key with `os.open(..., O_CREAT | O_EXCL, 0o600)`, and CI never
   writes it to disk at all — `.github/workflows/build.yml` reads it from the environment. Do
   not add a path that stages it in a workspace.

   **What "covered" means here: the `0600`-from-creation requirement applies; rule 2's routing
   through `write_private_file()` does not, and must not be applied to a key.** That helper ends
   in `os.replace`, which overwrites an existing file silently; `O_EXCL` fails on one instead,
   and `gen-signing-key.py` refuses up front as well. Replacing a release private key without
   the operator noticing is unrecoverable — every already-published signature stops verifying.

4. **Don't weaken the KDF.** New vaults use Argon2id at `ARGON2_MEMORY_KIB`,
   `ARGON2_ITERATIONS` and `ARGON2_LANES`, and those are the floor. They may be raised, never
   lowered. The salt stays 16 random bytes, unique per vault, generated with `os.urandom`.

   Raising them is a constant change: every `VLT2` header records the parameters it was
   written with, so an older vault still opens and is rewritten at its next unlock
   (`vault-format-and-crypto.md` INV-22, INV-24). A raise past an `ARGON2_MAX_*` cap is not:
   older builds report such a vault as too new (INV-23). Changing the algorithm or the header
   layout needs a **new magic** (`VLT3`), understood by `read_vault_header`, the one reader
   that unlock, Restore and `adopt_vault_file` share. `VLT1`'s PBKDF2 count, `ITERATIONS`, is
   fixed: that header has no field to record it (INV-5).

5. **Never persist the master password.** It lives only as a local variable / `self.password`
   for the session. No writing it to config, no environment variables, no clipboard.

6. **Preserve authenticated encryption.** Fernet gives confidentiality + integrity. Do not
   swap in a raw cipher, disable the HMAC, or hand-roll crypto. Use the `cryptography` library.

## Review checklist for security-relevant changes

Before merging anything that touches crypto, file I/O, import/export, the clipboard,
subprocesses, or the network and update code:

- [ ] No new code path writes a secret to disk in plaintext (outside the gated export).
- [ ] Every new or overwritten secret-bearing write routes through `write_private_file()` —
      `0600` from creation, `fsync` + `os.replace`. A trailing `chmod` does not satisfy this,
      because the file is readable until it runs. A signing key is the one exception:
      `O_CREAT | O_EXCL`, per non-negotiable 3.
- [ ] The master password is not logged, cached, or persisted.
- [ ] KDF parameters and salt handling are unchanged or raised within their caps. A new
      algorithm or header layout ships as a new magic, per non-negotiable 4.
- [ ] Imported/parsed input can't cause a crash that leaks state; parse errors surface as a
      dialog, not an unhandled traceback.
- [ ] Subprocess calls pass their arguments as a list, and `shell=True` is never used. A shell
      handed a command string (`sh -c`, `bash -c`, `powershell -Command`, `cmd /c`) never
      carries user or secret data. Two exist, and neither interpolates any: the updater's
      relaunch (`_relaunch_command`: this process's id and the `shlex.quote`d path of the app's
      own binary) and the Windows clipboard read (`powershell.exe -Command Get-Clipboard`, a
      fixed string). Secret data goes in over stdin, never as an argument, and anything whose
      output is awaited carries a timeout.
- [ ] No absolute personal paths are introduced (see file-naming standard).

## Input handling

- Treat imported text files as untrusted. `parse_text_file` must not execute or `eval`
  anything from the file; it only splits and regex-matches.
- Escape user-controlled strings before putting them in GTK markup
  (`GLib.markup_escape_text`) — already done for titles; keep it up for any new markup sink.

## Dependencies & supply chain

- Two dependencies are security-critical: `cryptography`, which does the vault's encryption
  and verifies update signatures, and `certifi`, the CA bundle that the frozen binaries' TLS
  trust — and so the update check — depends on. Both follow
  `dependency-management-standards.md` like any other: **latest stable by default**, with a
  forced-older pin allowed only through that standard's pin process, every step of it. Sitting
  on an older release because it is "current enough" breaches that standard.
  `certifi` is a bundled build-time dependency, not a `requirements.txt` entry, so a freshness
  sweep that reads only `requirements.txt` misses it. When bumping `cryptography`, skim its
  changelog for anything affecting Fernet, Argon2id or PBKDF2.
- `requirements.txt` sets a floor of `>=44.0.0` because older releases carry known CVEs, and
  **that floor overrides the forced-older-pin exception** — a pin below it is never permitted,
  ledger entry or not. A break fixable only by going below the floor is a release blocker.
- Adding any new dependency that handles secrets or does crypto requires explicit review — the
  default answer is "use the standard library or `cryptography`."

## Reporting

Security issues are reported privately per `SECURITY.md`, never in public issues or commit
messages.
