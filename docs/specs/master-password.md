# Spec: Master Password

Retroactive spec for creating, unlocking, and changing the master password (`UnlockDialog`,
`ChangePasswordDialog`, `MainWindow._finish_change_password`).

## Create (first run)

- **INV-1** When no vault file exists, `UnlockDialog` is in "create" mode and requires a
  password plus a matching confirmation.
- **INV-2** A new master password must be at least `MIN_PASSWORD_LENGTH` (8) characters;
  shorter passwords are rejected with an inline error.
- **INV-3** Mismatched password/confirmation is rejected with an inline error and no vault is
  created.
- **INV-4** On success a new empty encrypted vault is created (fresh salt) and the main window
  opens.

## Unlock (existing vault)

- **INV-5** When a vault file exists, `UnlockDialog` is in "unlock" mode requiring only the
  password. Enter activates the primary action.
- **INV-6** Decryption runs on a background thread; the button shows "Unlocking…" and is
  disabled while it runs, so the 600k-iteration KDF never freezes the UI.
- **INV-7** A wrong password (`InvalidToken`) shows "Wrong password.", re-enables the button,
  and refocuses the password field. Other errors show their message.
- **INV-8** On success the vault is migrated (`migrate_vault`) before use and the main window
  opens.

## Change master password

- **INV-9** Changing the password requires the correct current password, verified against the
  in-memory session password (`self.password`), not by re-decrypting the file.
- **INV-10** The new password must be ≥ 8 characters and match its confirmation; violations
  show an inline error and abort.
- **INV-11** On success the session password is replaced, a **new random salt** is generated,
  and the vault is re-encrypted and saved with the new password + salt.

## Notes

- Because the current-password check is against the in-memory value, changing the password is
  only reachable from an already-unlocked session — consistent with the app's single-session
  model.
- There is no recovery path; forgetting the master password means the vault is unrecoverable
  (see `SECURITY.md`).
- A stronger KDF (Argon2id) is roadmap ROLO-0005; auto-lock that would force re-entry of the
  password is ROLO-0002.
