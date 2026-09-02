# Spec: Auto-Lock on Idle, and Manual Lock

Retroactive spec for ROLO-0002 — the idle timer that locks the vault, the manual Lock action,
and what locking tears down. GUI layer: `_bump_activity`, `_start_idle_timer`, `_idle_check`,
`_lock`.

## Behaviour

### Configuration

- **INV-1** The timeout is `idle_lock_seconds` in `.rolodex.conf`, defaulting to
  `DEFAULT_IDLE_LOCK_SECONDS` (300 seconds). There is **no preferences UI** for it; the file is
  hand-edited.
- **INV-2** It is read through `config_int`, which returns the default rather than raising on a
  missing key or an unparseable value. That matters here specifically: the read happens in
  `MainWindow.__init__`, which runs from a `GLib.idle_add` callback after the unlock dialog has
  disabled its button — an exception there leaves the dialog stuck on "Unlocking..." forever
  with the vault decrypted in memory and nothing on screen explaining why.
- **INV-3** A value of **0 or less disables auto-lock entirely**: no timer is started, and an
  already-running one stops at its next tick. Manual lock still works.

### What counts as activity

- **INV-4** Activity is **pointer motion or a key press over the main window**. Both are
  observed through event controllers added to the window itself, and both handlers return
  `False`, so neither ever swallows the event.
- **INV-4a** The key controller runs in the **CAPTURE** propagation phase, not the default
  BUBBLE. This is load-bearing: in BUBBLE, a key consumed by the focused text widget — the
  search box, any dialog entry, the notes view — never reaches the handler, so **typing did not
  reset the activity clock and only mouse motion did**. A user composing a long note was locked
  out mid-edit, losing the open dialog. Any future activity source must be attached the same
  way.
- **INV-5** Activity updates a monotonic timestamp only. It does **not** restart or reschedule
  the timer, so the polling cadence is independent of how the user behaves.
- **INV-6** The clock is `GLib.get_monotonic_time()`, not wall time, so a system clock change
  cannot bring the lock forward or push it away.

### The timer

- **INV-7** The poll interval is `max(5, min(30, idle_lock_seconds))` seconds — so at least
  every 30 seconds, and never more often than every 5. The check samples the window rather than
  counting down to it.
- **INV-8** Because the check samples, the lock fires at the first tick **at or after** the
  timeout, not exactly on it. The overshoot is bounded by the interval of INV-7.
- **INV-9** The check locks when elapsed monotonic time is `>=` the timeout. It stops itself and
  does nothing if the timeout has been disabled, or if `self.vault` is already `None` — the
  latter meaning the window is already locked.
- **INV-10** Starting the timer removes any existing source first, so it cannot be armed twice.

### Manual lock

- **INV-11** A `win.lock` action locks on demand, bound to **Ctrl+L**. It runs the identical
  teardown; there is no second code path.

### What locking tears down

- **INV-12** In order: the idle timer is removed; the search debounce is cancelled; anything the
  update path has in flight is cancelled; **the clipboard is cleared** per
  `clipboard-auto-clear.md`; the rendered detail pane is emptied; the current entry id is
  dropped; and `vault`, `salt`, `password` and the cached key are all set to `None`.
- **INV-13** Emptying the detail pane is not cosmetic. It holds the last-viewed values as label
  text and one copy closure per field, so clearing `self.vault` alone would leave them
  reachable.
- **INV-14** Nothing unsaved can be lost at lock, because every mutation saves immediately and
  a failed save is reported to the user rather than escaping silently.
- **INV-15** The window then closes and the unlock dialog is presented. Closing is what cancels
  the TOTP tick (`totp-codes.md` INV-22), so a lock cannot leave a timer deriving codes from a
  vault that is gone.

## Notes

- Locking discards state; it does not save. The save-on-every-mutation rule of
  `vault-format-and-crypto.md` is what makes that safe.
- The bound on INV-7 is a deliberate trade: polling every second for a five-minute timeout buys
  nothing, and a very short configured timeout still gets checked promptly because the interval
  floors at 5 seconds rather than at the timeout.
- Auto-lock protects an unattended unlocked session. It is not a defence against a compromised
  machine — `SECURITY.md` says so outright, since the decrypted vault is in process memory for
  as long as the session is open.
