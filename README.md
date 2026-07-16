# Rolodex

A minimal, single-file **encrypted credential manager** for the Linux desktop, built with
GTK 4 and libadwaita. One master password unlocks a local, encrypted vault of logins,
API keys, and notes — organised into categories, searchable, with sensitive fields masked
until you reveal them.

No cloud, no sync, no telemetry. Your data lives in one encrypted file on your machine.

![Rolodex application icon.](rolodex.png)

## Features

- **Encrypted-at-rest vault** — AES (via Fernet) with a key derived from your master
  password using PBKDF2-HMAC-SHA256 (600,000 iterations) and a per-vault random salt.
- **Categories** — group entries, collapse/expand sections, drag entries between categories.
- **Sensitive-field masking** — password/token/secret fields are hidden behind dots and
  auto-detected from their label; reveal per-entry when you need them.
- **Password generator** — generate a strong random password (length + character-class
  options) right in the add/edit editor, powered by Python's `secrets` module.
- **One-click copy** to the clipboard (Wayland `wl-copy`, or X11 `xclip`/`xsel`), which is
  **cleared automatically** a few seconds later so secrets don't linger.
- **Auto-lock on idle** — the vault re-locks itself after a period of inactivity (and on
  demand via a Lock button / `Ctrl+L`), wiping the decrypted data from memory.
- **Import** from a simple `Name:` / `Label: value` text format, with a preview + de-dupe step.
- **Encrypted backup & restore**, plus an optional plaintext export.
- **Colour-coded fields** — credentials, keys, identities, URLs, and dates each get a
  distinct accent so a card is scannable at a glance.

## Download (prebuilt binaries)

Self-contained builds for Linux, Windows, and macOS are published on the
[Releases page](https://github.com/milnet01/rolodex/releases) — each is a single file with
Python, GTK, and all dependencies bundled in, so there's nothing else to install.

| Platform | File | Notes |
|----------|------|-------|
| Linux (x86-64) | `rolodex-linux-x86_64` | `chmod +x` it, then run it. Built on Ubuntu 24.04; needs a reasonably recent glibc. |
| macOS (Apple Silicon) | `rolodex-macos-arm64` | Unsigned — the first time, **right-click → Open** to get past Gatekeeper, then confirm. |
| Windows (x64) | _in progress_ | Not yet available — the GTK bundle doesn't load on Windows yet (tracked as ROLO-0031). Run from source for now. |

The packaged app stores your vault in a per-user data directory (`~/.local/share/Rolodex` on
Linux, `~/Library/Application Support/Rolodex` on macOS, `%APPDATA%\Rolodex` on Windows), not
next to the executable.

Prefer to run from source instead? Read on.

## Requirements

- Python 3.10+
- GTK 4 and libadwaita with GObject introspection (PyGObject / `gi`)
- The Python [`cryptography`](https://pypi.org/project/cryptography/) package

On openSUSE:

```bash
sudo zypper install python3-gobject gtk4 libadwaita python3-cryptography
```

On Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-cryptography
```

On Fedora:

```bash
sudo dnf install python3-gobject gtk4 libadwaita python3-cryptography
```

The GTK stack must come from the system (it's not on PyPI). The one pure-Python dependency,
`cryptography`, can alternatively be installed with pip — always the latest version:

```bash
pip install -U -r requirements.txt
```

## Running

```bash
python3 rolodex.py
```

On first launch you'll be asked to create a master password (minimum 8 characters). The
vault file `contacts.vault` is created next to the script with `0600` permissions.

> **There is no password recovery.** The master password is never stored — it only exists
> as the key that decrypts your vault. If you forget it, the data is unrecoverable. Keep an
> encrypted backup (menu → *Backup vault…*).

## Desktop launcher

A `rolodex.desktop` file is included. Edit its `Exec=` and `Icon=` lines to the absolute
path where you cloned this repo, then install it:

```bash
# from the repo directory:
sed -i "s|/path/to/rolodex|$PWD|g" rolodex.desktop   # point Exec/Icon at this clone
cp rolodex.desktop ~/.local/share/applications/
```

## Data & files

| File | Purpose |
|------|---------|
| `rolodex.py` | The entire application. |
| `requirements.txt` | The single pip dependency (`cryptography`). |
| `rolodex.desktop` | Desktop launcher template (edit its paths — see above). |
| `contacts.vault` | Your encrypted vault (created on first run; **git-ignored**). |
| `Backups/`, `rolodex_export_*.txt` | Backup copies and plaintext exports you create (git-ignored). |
| `.rolodex.conf` | Window geometry and non-secret preferences (security timeouts), plaintext (git-ignored). |
| `rolodex.png` | Application icon. |

The vault format is `VLT1` magic bytes + 16-byte salt + Fernet ciphertext (canonical spec:
`docs/specs/vault-format-and-crypto.md`).

## Security notes

- The vault is encrypted with a modern authenticated cipher and a slow KDF, but its
  strength ultimately rests on your master password — choose a strong one.
- This is a personal-use tool, not audited security software. Review the code (it's one
  readable file) before trusting it with anything critical.
- Two hardening timeouts are configurable in `.rolodex.conf` (plaintext JSON, no secrets):
  `idle_lock_seconds` (auto-lock delay, default 300; `0` disables) and `clipboard_clear_seconds`
  (clipboard wipe delay, default 20; `0` disables).

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the ground rules
(one-file app, minimal dependencies) and [SECURITY.md](SECURITY.md) for reporting security
issues privately.

## License

[MIT](LICENSE) © 2026 Anthony Schemel
