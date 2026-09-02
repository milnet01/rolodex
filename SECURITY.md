# Security Policy

Rolodex stores credentials. Security is the whole point of the app, so this document
describes both how it protects data and how to report a problem.

## Threat model

**What Rolodex protects against**

- **Theft of the vault file at rest.** `contacts.vault` is encrypted; without the master
  password it is a blob of ciphertext. An attacker who copies the file must brute-force the
  master password against a deliberately slow key-derivation function.
- **Casual shoulder-surfing.** Sensitive fields are masked in the UI until explicitly revealed.

**What Rolodex does *not* protect against**

- **A compromised machine.** Once the vault is unlocked, the master password and decrypted
  data live in this process's memory in plaintext. Malware, a memory scraper, or a root user
  on the same machine can read them. Rolodex is a data-at-rest tool, not an anti-malware tool.
- **A weak master password.** The encryption is only as strong as the password. There is no
  server-side rate limiting — an attacker with the file can guess offline as fast as their
  hardware allows (slowed only by the KDF).
- **Clipboard exposure.** Copied secrets go to the system clipboard, which other applications
  can read and which may be synced by the desktop environment. Clear it after use.
- **Plaintext export.** The *Export (decrypted plaintext)* feature writes an unencrypted file
  by design. Treat that output as radioactive.

### The update check (ROLO-0037)

Rolodex has one optional network feature: a check for a newer release. It is **off by
default** — nothing runs automatically until you enable it — with one deliberate exception:
choosing *Check for updates...* from the menu checks once regardless of the setting, because
an explicit request is its own consent. This is what that check does and does not do.

**What it protects against.** A malicious or compromised download. Every update is verified
against an Ed25519 public key compiled into your binary, over the exact bytes downloaded. A
tampered binary, a wrong-key signature, or a missing signature all result in nothing being
installed. TLS alone would not be enough here: it authenticates the host, not the artifact,
and would put the whole update path inside GitHub account security and the CA system.

**What it does not protect against.** Loss of the release-signing private key. Anyone holding
it can sign an update your binary will accept, so it lives in a repository secret and a backup
and nowhere else. It also does not hide *that* you check: GitHub sees the request, which is
why the feature is opt-in rather than assumed.

**What it never sends.** Nothing derived from your vault. The request carries a fixed
`User-Agent` and no identifier, account, or query string. The check reads only the plaintext
`.rolodex.conf` and the app's own version, so it runs while the vault is locked and never
touches the master password.

**Its current state.** Until a signing key is generated, the built-in key is an all-zero
placeholder that verifies nothing — so the feature can offer an update and can never install
one. It fails closed rather than open, deliberately.

## Cryptographic design

| Element | Choice |
|---------|--------|
| Key derivation | PBKDF2-HMAC-SHA256, 600,000 iterations |
| Salt | 16 random bytes per vault, stored in the clear in the file header |
| Encryption | Fernet (AES-128-CBC + HMAC-SHA256, authenticated) |
| File format | `VLT1` magic (4 B) + salt (16 B) + Fernet token |
| File permissions | `0600` (owner read/write only) — every secret file (vault, export, backup) is created `0600` and moved into place atomically, so it is never briefly readable by anyone else |

Changing the master password rotates the salt and re-encrypts the entire vault.

There is **no password recovery mechanism** — this is intentional. The master password is
never persisted; it exists only transiently as the decryption key.

## Reporting a vulnerability

If you find a security issue, **please do not open a public GitHub issue.**

Instead, use GitHub's private vulnerability reporting:
**Security → Report a vulnerability** on the repository page.

Please include a description, reproduction steps, and the affected version/commit. You can
expect an initial acknowledgement within a reasonable time for a personal-scale project.
Since this is a single-maintainer hobby project, there are no formal SLAs, but security
reports are taken seriously and triaged first.

## Scope

In scope: the encryption/KDF implementation, file-permission handling, the import/export
paths, and any way to recover secrets without the master password.

Out of scope: attacks that assume an already-compromised host or a weak user-chosen master
password (see threat model above).
