# Spec: Clipboard Auto-Clear

Retroactive spec for ROLO-0003 — copying a secret to the system clipboard and wiping it again.
Pure layer: `copy_to_clipboard`, `read_clipboard`. GUI layer: `_copy_value`,
`_clear_clipboard_if_unchanged`, `_cancel_clipboard_timer`, `_clear_clipboard_on_lock`.

## Behaviour

### Configuration

- **INV-1** The delay is `clipboard_clear_seconds` in `.rolodex.conf`, defaulting to
  `DEFAULT_CLIPBOARD_CLEAR_SECONDS` (20 seconds), read through `config_int` (see
  `auto-lock.md` INV-2). There is no preferences UI; the file is hand-edited.
- **INV-2** A value of **0 or less disables the timer** but does not disable the feature: the
  copied value is still remembered, so a lock can still wipe it.

### Copying

- **INV-3** `copy_to_clipboard` tries `pbcopy`, `wl-copy --trim-newline`, `xclip`, `xsel` and
  `clip.exe` in that order, skipping any tool not on `PATH`. The secret is passed on **stdin**,
  never as an argument, with a 5-second timeout and no shell.
- **INV-4** A tool that exits non-zero **falls through to the next one**. Returning on first
  invocation meant that merely having wl-clipboard installed under an X11 session — which
  several distributions arrange by default — made every copy fail, because `wl-copy` exits
  non-zero with no Wayland display while a working `xclip` sat untried.
- **INV-5** If no tool succeeds, the user is told "Clipboard not available", no timer is armed
  and no value is remembered. A failed copy must not schedule a wipe.
- **INV-6** On success the copied value is recorded as pending **whatever the delay**, any
  previous timer is cancelled first, and a toast confirms. The toast names the delay when there
  is one.

### Clearing

- **INV-7** The timer is one-shot. When it fires it clears the clipboard **only if the
  clipboard still holds what we put there.**
- **INV-8** The comparison reads the clipboard back with `read_clipboard`, which mirrors the
  writer's tool priority so a read pairs with the tool used for the copy. The current contents
  match if they equal the copied value **or** that value with trailing newlines stripped —
  `wl-copy --trim-newline` stores such a value trimmed, and without the second comparison a
  value ending in a newline would never match and the secret would stay on the clipboard
  permanently.
- **INV-9** If the clipboard has moved on, it is left alone and the pending value is dropped.
  The user's newer copy is not destroyed.
- **INV-10** If **no reader is available at all**, the wipe proceeds anyway. For a credential
  manager, clearing a clipboard that cannot be inspected is the safe direction.
- **INV-11** Wiping means writing an empty string through the same tool chain.

### Locking

- **INV-12** A lock clears the clipboard before discarding the vault: the pending timer is
  cancelled and the same still-unchanged check of INV-7 to INV-10 is applied immediately.
- **INV-13** The timer alone cannot cover this. It may still be pending, and under INV-2 there
  is no timer at all — so without an explicit clear at lock the secret would sit on the
  clipboard indefinitely.

## Notes

- The clipboard is outside the app's trust boundary, and `SECURITY.md` lists clipboard exposure
  as something Rolodex does **not** protect against: another application can read the clipboard
  while the secret is on it, and a desktop environment may sync it. Auto-clear shortens the
  window; it does not close it.
- INV-9's leave-it-alone rule is why the pending value is compared rather than the clipboard
  simply being blanked on a schedule. Blanking unconditionally would destroy whatever the user
  copied next.
- The live 2FA code copies through this same path (`totp-codes.md` INV-20), so a copied code is
  cleared on the same terms as any other secret.
