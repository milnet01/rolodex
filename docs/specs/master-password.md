# Spec: Master Password

Retroactive spec for creating, unlocking, and changing the master password (`UnlockDialog`,
`ChangePasswordDialog`, `MainWindow._finish_change_password`).

## Create (first run)

- **INV-1** When no vault file exists, `UnlockDialog` is in "create" mode and requires a
  password plus a matching confirmation.
- **INV-2** A new master password must be at least `MIN_PASSWORD_LENGTH` (12) characters;
  shorter passwords are rejected with an inline error.
- **INV-3** Mismatched password/confirmation is rejected with an inline error and no vault is
  created.
- **INV-4** On success a new empty encrypted vault is created (fresh salt) and the main window
  opens.

## Unlock (existing vault)

- **INV-5** When a vault file exists, `UnlockDialog` is in "unlock" mode requiring only the
  password. Enter activates the primary action.
- **INV-6** Decryption runs on a background thread; the button shows `"Unlocking..."` (three
  ASCII dots) and is disabled while it runs, so the 600k-iteration KDF never freezes the UI.
- **INV-7** A wrong password (`InvalidToken`) shows "Wrong password.", re-enables the button,
  and refocuses the password field. Other errors show their message.
- **INV-7a** A load that fails with `ValueError` — bad magic, a truncated salt, contents that are
  not a vault — also reveals "Restore from Backup…" and "Start a New Vault…". A wrong password
  never reveals them. Restore installs a chosen vault file after checking its header; Start
  creates a new vault. Both first rename the unreadable file to `<vault>.unreadable-<time>`
  beside it, and neither deletes it (ROLO-0045).
- **INV-7b** In create mode the same restore button reads "Use an Existing Vault File…", so a
  user whose vault lives elsewhere — a source checkout, before a move to the packaged build —
  can adopt it instead of creating an empty one (ROLO-0078).
- **INV-7c** Unlocking and creating each take the vault lock of `vault-format-and-crypto.md`
  INV-18 first. When another session holds it, the dialog shows "This vault is already open in
  another Rolodex window." and does not unlock (ROLO-0044).
- **INV-8** On success the vault is migrated (`migrate_vault`) before use and the main window
  opens.

## Change master password

- **INV-9** Changing the password requires the correct current password, verified against the
  in-memory session password (`self.password`), not by re-decrypting the file. A wrong current
  password shows "Incorrect current password." and aborts.
- **INV-10** The new password must be ≥ `MIN_PASSWORD_LENGTH` characters and match its
  confirmation; violations show an inline error and abort.
- **INV-11** On success a **new random salt** is generated and the vault is re-encrypted and
  written with the new password + salt **first**. The session password, salt and key are
  adopted only once that write has landed; a failed write leaves the session on the old pair
  and shows "Password Not Changed". The old salt/password no longer decrypt `contacts.vault`,
  but any backup made *before* the change still opens with the old password.

## Notes

- Because the current-password check is against the in-memory value, changing the password is
  only reachable from an already-unlocked session — consistent with the app's single-session
  model.
- There is no recovery path; forgetting the master password means the vault is unrecoverable
  (see `SECURITY.md`).
- A stronger KDF (Argon2id) is roadmap ROLO-0005; auto-lock that would force re-entry of the
  password is ROLO-0002.
