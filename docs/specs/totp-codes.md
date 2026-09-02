# Spec: TOTP / Live 2FA Codes

Retroactive spec for ROLO-0006 — the detection of a 2FA seed in a stored field, the RFC 6238
code derived from it, and the live "Code" row in the detail pane. Pure layer:
`TOTP_LABEL_KEYWORDS`, `_decode_base32`, `totp_code`, `totp_remaining`, `_parse_otpauth_uri`,
`parse_totp_field`, `field_is_sensitive`. No new dependency — stdlib `hmac`/`hashlib` only.

## Behaviour

### What qualifies as a 2FA field

- **INV-1** `parse_totp_field(label, value)` returns a config
  `{secret, digits, period, algorithm}` or `None`. It is **pure and total**: it never raises on
  user data, whatever is stored in the field.
- **INV-2** A value beginning `otpauth://` (case-insensitive) is parsed as a Key URI and
  qualifies **under any label**. The scheme must be `otpauth` and the host must be `totp`;
  counter-based HOTP is out of scope and returns `None`.
- **INV-3** Any other value qualifies **only if** the label contains one of
  `TOTP_LABEL_KEYWORDS` — `authenticator`, `2fa`, `totp`, `otp`, `one-time`, `one time` —
  matched case-insensitively as a substring. Without a keyword a bare base32 value is left
  alone, so a random base32-looking password does not sprout a fake code.
- **INV-4** A bare seed must decode to **at least 10 bytes (80 bits)**. This is a heuristic
  threshold, not an RFC floor — see Notes.
- **INV-5** A bare seed always uses `digits=6`, `period=30`, `algorithm=sha1`. There is nowhere
  in a bare value to carry anything else.

### Decoding a base32 secret

- **INV-6** `_decode_base32` tolerates lower case, missing `=` padding, and any run of
  whitespace or `-` inside the seed. It returns `None` rather than raising, so "not a seed" and
  "malformed seed" are indistinguishable to the caller.
- **INV-7** The value is validated against `[A-Za-z2-7]*` **before** case folding, and against
  the ASCII ranges only. `str.upper()` applies full Unicode case mapping, so characters outside
  base32 fold *into* it (`ı` → `I`, `ſ` → `S`) and would otherwise decode silently to a
  **different secret** rather than failing. Validating after the fold cannot catch this.
- **INV-8** An empty decode result is `None`, not `b""`.

### Parsing an `otpauth://` URI

- **INV-9** A malformed URI returns `None`. `urlparse` raises on an unbalanced bracketed host
  (`otpauth://[totp`), and that exception is caught: this runs per field from the detail-view
  render, so an escape would strand the whole entry behind an undrawable pane.
- **INV-10** `secret=` is required and must decode. `algorithm=` defaults to `SHA1` and must be
  one of `sha1`, `sha256`, `sha512`. `digits=` defaults to `6`; `period=` defaults to `30`.
  A non-integer `digits` or `period` returns `None`.
- **INV-11** `digits` must be 6, 7 or 8, and `period` must be between 1 and 300 inclusive.
  Outside those ranges the field does not qualify. Dynamic truncation yields at most
  2,147,483,647, so a 9- or 10-digit code is degenerate — its leading digits could never span
  their full range — and an unbounded period produces a countdown the ring cannot render.
- **INV-12** The `>= 10 bytes` floor of INV-4 does **not** apply to a URI. A URI is an explicit
  declaration by the user's authenticator, not a guess off a bare value.

### Computing the code

- **INV-13** `totp_code` is RFC 6238: counter = `int(timestamp) // period`, HMAC over the
  counter packed big-endian as 8 bytes, dynamic truncation on the low nibble of the last MAC
  byte, masked to 31 bits, modulo `10 ** digits`, zero-padded to `digits`.
- **INV-14** `totp_remaining(timestamp, period)` is `period - int(timestamp) % period`. It
  equals `period` exactly on a window boundary, never 0.

### Masking

- **INV-15** `field_is_sensitive(field)` is true when the stored `sensitive` flag is set **or**
  `parse_totp_field` recognises the field. The two keyword sets do not agree —
  `SENSITIVE_KEYWORDS` and `TOTP_LABEL_KEYWORDS` overlap only on `authenticator` — so without
  this a field labelled `2FA`, `TOTP`, `OTP` or `One-time` was stored non-sensitive and then
  rendered in permanent cleartext beside the live code derived from it.
- **INV-16** This is checked **at render as well as at save**, so entries already sitting in a
  vault are masked too, not only newly edited ones.

### The live Code row

- **INV-17** The detail pane injects one **"Code"** row per qualifying field, immediately after
  the field it derives from. A non-qualifying field gets no row.
- **INV-18** The row shows the code **grouped into two halves separated by a single space** —
  `492 831` for six digits, `4920 8317` for eight. The displayed string is not the code; a
  check asserting contiguous digits fails against a working feature.
- **INV-19** The row also carries a ring that empties clockwise as the window elapses, a
  `<n>s` countdown label, and a copy button.
- **INV-20** The copy button copies the **ungrouped** code — the digits only, no space — and
  routes through the ordinary copy path, so the clipboard auto-clear of
  `clipboard-auto-clear.md` applies to it.
- **INV-21** **One** shared 1-second `GLib.timeout` refreshes every visible code row, whatever
  their periods. It starts only if at least one code row was built, and the first refresh runs
  immediately rather than on the timer's first fire — otherwise every code shows blank for a
  second after the entry opens.
- **INV-22** The timer is cancelled before every detail rebuild, and on window close — which is
  the path a lock takes. Leaving it running leaks a timer across entries and keeps deriving
  codes from a vault that has been closed.

## Notes

- **The 80-bit floor is deliberate and is not the RFC's.** RFC 4226 §4 R6 requires at least 128
  bits and recommends 160. Google Authenticator's standard 16-character seed decodes to 80, so
  enforcing the RFC would reject the most common seed in existence. The floor exists to stop
  short base32-valid prose — `just some words` — being mistaken for a seed when guessing off a
  bare value, and it is why INV-12 exempts an explicit URI.
- Codes are derived in-process from the decrypted vault. Nothing is sent anywhere, and no seed
  is written outside the vault.
- HOTP is out of scope by INV-2. Adding it would need a stored, mutable counter, which the
  entry model has no field for.
