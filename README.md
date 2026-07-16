# Rolodex

**A safe, simple place to keep your passwords, keys, and private notes — on your own computer.**

Rolodex tucks all your logins, API keys, and secret notes inside a single encrypted file. One
master password unlocks it. There's no cloud, no account to sign up for, and no tracking — your
data never leaves your machine. It runs on Linux, Windows, and macOS.

![Rolodex application icon.](rolodex.png)

## What it does

- **Everything stays encrypted.** Your whole vault is scrambled with strong, industry-standard
  encryption and can only be opened with your master password. *(For the technically curious:
  AES via Fernet, with the key derived from your password using PBKDF2-HMAC-SHA256 at 600,000
  rounds and a random per-vault salt.)*
- **Organise into categories.** Group entries (Email, Banking, Games…), collapse the sections
  you're not using, and drag entries from one category to another.
- **Find anything fast.** Search as you type across names, fields, and notes.
- **Secrets stay hidden.** Passwords and keys are masked behind dots and reveal only when you
  ask. Rolodex recognises sensitive-looking fields on its own.
- **Built-in password generator.** Create a strong random password in a click, with control
  over its length and which kinds of characters to include.
- **Safer copying.** Copy a password with one click — and Rolodex wipes it from the clipboard a
  few seconds later, so it doesn't sit there for other apps to read.
- **Auto-locks when you step away.** After a stretch of inactivity (or instantly with the Lock
  button or `Ctrl+L`), Rolodex re-locks and forgets your master password until you unlock again.
- **Import your existing list** from a plain text file, with a preview and a skip-duplicates step.
- **Backup & restore**, plus a plain-text export if you ever want to move your data elsewhere.
- **Change your master password** whenever you like.
- **Easy to scan.** Different kinds of fields — logins, keys, web addresses, dates — each get
  their own colour accent so a card reads at a glance.

## Download & run

Ready-to-run downloads for **Linux, Windows, and macOS** are on the
[**Releases page**](https://github.com/milnet01/rolodex/releases). Each is a **single file**
with everything bundled inside — there's nothing else to install.

| Your system | Download | How to start it |
|-------------|----------|-----------------|
| **Linux** (x86-64) | [`rolodex-linux-x86_64`](https://github.com/milnet01/rolodex/releases/latest/download/rolodex-linux-x86_64) | Make it runnable — `chmod +x rolodex-linux-x86_64` — then run it. Works on most current Linux systems. |
| **Windows** (64-bit) | [`rolodex-windows-x86_64.exe`](https://github.com/milnet01/rolodex/releases/latest/download/rolodex-windows-x86_64.exe) | Double-click it. It isn't signed yet, so Windows may show a blue "protected your PC" box — click **More info → Run anyway**. |
| **macOS** (Apple Silicon) | [`rolodex-macos-arm64`](https://github.com/milnet01/rolodex/releases/latest/download/rolodex-macos-arm64) | It isn't signed yet, so the first time **right-click → Open**, then confirm. After that it opens normally. |

Your vault is saved in your personal data folder (`~/.local/share/Rolodex` on Linux,
`~/Library/Application Support/Rolodex` on macOS, `%APPDATA%\Rolodex` on Windows), not next to
the download.

## First launch

The first time you open Rolodex, you'll create your **master password** (at least 8
characters). This one password protects everything.

> ⚠️ **There is no way to recover a forgotten master password.** It is never saved anywhere —
> it's the only key to your data. If you lose it, the data is gone for good. Make a backup you
> can restore from (menu → *Backup vault…*) and keep your master password somewhere safe.

## Run from source instead (optional)

Prefer to run the code directly? You'll need a few things first:

- Python 3.10 or newer
- GTK 4 and libadwaita — the desktop toolkit Rolodex is built with — via PyGObject
- The Python [`cryptography`](https://pypi.org/project/cryptography/) package

Install the toolkit from your system's package manager:

```bash
# openSUSE
sudo zypper install python3-gobject gtk4 libadwaita python3-cryptography

# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-cryptography

# Fedora
sudo dnf install python3-gobject gtk4 libadwaita python3-cryptography
```

The desktop toolkit has to come from your system (it isn't on PyPI). The one remaining piece,
`cryptography`, can also be installed with pip — always the latest version:

```bash
pip install -U -r requirements.txt
```

Then start it:

```bash
python3 rolodex.py
```

## Add it to your app menu (Linux)

A `rolodex.desktop` launcher is included. Point it at your copy of the repo and install it:

```bash
# from the folder where you cloned this repo:
sed -i "s|/path/to/rolodex|$PWD|g" rolodex.desktop   # point it at this copy
cp rolodex.desktop ~/.local/share/applications/
```

## Where your things are kept

| File | What it is |
|------|------------|
| `rolodex.py` | The whole application (it's a single file). |
| `contacts.vault` | Your encrypted vault — created on first run. Never shared or committed. |
| `.rolodex.conf` | Window size plus a couple of non-secret settings (see below). Plain text, no secrets. |
| `Backups/`, `rolodex_export_*.txt` | Backups and exports you create. |
| `requirements.txt` | The single pip dependency (`cryptography`). |
| `rolodex.desktop` | The app-menu launcher (edit its paths — see above). |
| `rolodex.png` | The app icon. |

## Security & privacy

- Your vault is protected with well-established encryption, but its real strength is **your
  master password** — pick a strong one.
- The vault file can be read only by your own user account, and your master password is never
  written to disk.
- Two safety timers can be tuned in `.rolodex.conf` (plain text, no secrets): how long before it
  auto-locks (`idle_lock_seconds`, default 300 seconds; set `0` to turn off) and how soon a
  copied password is wiped from the clipboard (`clipboard_clear_seconds`, default 20; `0` to
  turn off).
- This is a personal-use tool, not audited security software. It's one readable file — feel free
  to look before trusting it with anything critical. The nuts-and-bolts of the vault format are
  documented in [`docs/specs/vault-format-and-crypto.md`](docs/specs/vault-format-and-crypto.md).

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the ground rules
(one-file app, minimal dependencies) and [SECURITY.md](SECURITY.md) for reporting security
issues privately.

## License

[MIT](LICENSE) © 2026 Anthony Schemel
