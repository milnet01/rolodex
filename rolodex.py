#!/usr/bin/env python3
"""Rolodex - Encrypted credential manager with GTK4/Adwaita GUI."""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import string
import struct
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime

import gi

# gi.require_version() must run before the gi.repository imports, so these imports
# cannot sit at the top of the file; E402 is silenced on exactly those lines.
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_ID = "com.rolodex.Contacts"
if getattr(sys, "frozen", False):
    # Packaged (PyInstaller) build: __file__ lives in a temp extraction dir that is deleted on
    # exit, so persist user data in the per-user data directory — ~/.local/share/Rolodex on
    # Linux, ~/Library/Application Support/Rolodex on macOS, %APPDATA%\Rolodex on Windows.
    APP_DIR = os.path.join(GLib.get_user_data_dir(), "Rolodex")
    os.makedirs(APP_DIR, exist_ok=True)
else:
    # Running from source: keep data next to the script (portable, unchanged behaviour).
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT_FILE = os.path.join(APP_DIR, "contacts.vault")
CONFIG_FILE = os.path.join(APP_DIR, ".rolodex.conf")
MAGIC = b"VLT1"
ITERATIONS = 600_000
SENSITIVE_KEYWORDS = {"password", "pass", "secret", "key", "token", "pin", "authenticator"}
MIN_PASSWORD_LENGTH = 8
MASK = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"

# Password generator (ROLO-0004): character classes and default length.
PW_GEN_LENGTH = 20
PW_GEN_SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?"
PW_GEN_CLASSES = {
    "lower": string.ascii_lowercase,
    "upper": string.ascii_uppercase,
    "digits": string.digits,
    "symbols": PW_GEN_SYMBOLS,
}

# Security timeouts, both user-configurable via .rolodex.conf (0 disables).
DEFAULT_CLIPBOARD_CLEAR_SECONDS = 20  # ROLO-0003: wipe a copied secret after this delay
DEFAULT_IDLE_LOCK_SECONDS = 300  # ROLO-0002: auto-lock after this much inactivity

# ROLO-0018: coalesce rapid search keystrokes — rebuild the list once typing pauses, rather
# than on every character (each rebuild re-scans every entry).
SEARCH_DEBOUNCE_MS = 150

# ---------------------------------------------------------------------------
# Encryption layer
# ---------------------------------------------------------------------------


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def write_private_file(path: str, data: bytes) -> None:
    """Write bytes to path with owner-only (0600) permissions, creating or truncating it.

    Every secret-writing path (the vault, the plaintext export) goes through here so the
    0600 mode and the fd-ownership dance live in exactly one place (ROLO-0019).
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        fp = os.fdopen(fd, "wb")
    except Exception:
        os.close(fd)  # fdopen didn't take ownership of the fd; close it ourselves
        raise
    with fp:
        fp.write(data)


def save_vault(vault_data: dict, password: str, salt: bytes, path: str) -> None:
    key = derive_key(password, salt)
    f = Fernet(key)
    plaintext = json.dumps(vault_data, ensure_ascii=False).encode("utf-8")
    ciphertext = f.encrypt(plaintext)
    write_private_file(path, MAGIC + salt + ciphertext)


def load_vault(password: str, path: str) -> tuple[dict, bytes]:
    with open(path, "rb") as fp:
        magic = fp.read(4)
        if magic != MAGIC:
            raise ValueError("Not a valid vault file")
        salt = fp.read(16)
        ciphertext = fp.read()
    key = derive_key(password, salt)
    f = Fernet(key)
    plaintext = f.decrypt(ciphertext)
    return json.loads(plaintext.decode("utf-8")), salt


def create_vault(password: str, path: str) -> tuple[dict, bytes]:
    salt = os.urandom(16)
    vault_data = {"version": 2, "categories": [], "entries": {}}
    save_vault(vault_data, password, salt, path)
    return vault_data, salt


def migrate_vault(vault: dict) -> dict:
    """Upgrade vault data to the latest version (v2). Idempotent."""
    if "categories" not in vault:
        vault["categories"] = []
    for entry in vault["entries"].values():
        if "category" not in entry:
            entry["category"] = ""
    vault["version"] = 2
    return vault


# ---------------------------------------------------------------------------
# Data operations
# ---------------------------------------------------------------------------


def is_sensitive_label(label: str) -> bool:
    label_lower = label.lower()
    return any(kw in label_lower for kw in SENSITIVE_KEYWORDS)


# Field category classification — order matters (first match wins)
FIELD_CATEGORIES = [
    ("credential", {"password", "pass", "pin", "authenticator", "guard"}),
    ("key",        {"key", "token", "secret"}),
    ("identity",   {"username", "user", "email", "mail", "account", "id", "gamertag", "tag"}),
    ("url",        {"url", "website", "link", "domain", "http"}),
    ("date",       {"date", "expires", "expiry", "plus", "subscription", "renewal", "expire"}),
]


def field_category(label: str) -> str:
    """Classify a field label into a category for color-coding."""
    label_lower = label.lower()
    for category, keywords in FIELD_CATEGORIES:
        if any(kw in label_lower for kw in keywords):
            return category
    return "other"


# TOTP / 2FA codes (ROLO-0006) — pure RFC 6238, no new dependency (stdlib hmac/hashlib).
# A bare base32 seed only becomes a live code when its label hints 2FA; an otpauth:// URI
# always qualifies. This keeps a random base32-looking password from sprouting a fake code.
TOTP_LABEL_KEYWORDS = {"authenticator", "2fa", "totp", "otp", "one-time", "one time"}
_TOTP_HASHES = {"sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512}


def _decode_base32(s: str) -> bytes | None:
    """Decode a base32 secret, tolerating lower-case, spaces/dashes, and missing padding.

    Returns None (rather than raising) on anything that isn't valid base32, so the detection
    path can treat "not a seed" and "malformed seed" identically.
    """
    cleaned = s.strip().replace(" ", "").replace("-", "").upper()
    if not cleaned:
        return None
    padded = cleaned + "=" * (-len(cleaned) % 8)
    try:
        decoded = base64.b32decode(padded, casefold=True)  # binascii.Error subclasses ValueError
    except ValueError:
        return None
    return decoded or None


def totp_code(secret: bytes, timestamp: float, digits: int = 6,
              period: int = 30, algorithm: str = "sha1") -> str:
    """Compute the RFC 6238 TOTP code for a raw (base32-decoded) secret at a unix time."""
    counter = int(timestamp) // period
    mac = hmac.new(secret, struct.pack(">Q", counter), _TOTP_HASHES[algorithm]).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def totp_remaining(timestamp: float, period: int = 30) -> int:
    """Seconds left in the current code's window (equals period exactly on a boundary)."""
    return period - int(timestamp) % period


def _parse_otpauth_uri(uri: str) -> dict | None:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "otpauth" or parsed.netloc.lower() != "totp":
        return None  # only time-based OTP; HOTP (counter-based) is out of scope
    q = urllib.parse.parse_qs(parsed.query)
    secret = _decode_base32((q.get("secret") or [""])[0])
    if not secret:
        return None
    algorithm = (q.get("algorithm") or ["SHA1"])[0].lower()
    if algorithm not in _TOTP_HASHES:
        return None
    try:
        digits = int((q.get("digits") or ["6"])[0])
        period = int((q.get("period") or ["30"])[0])
    except ValueError:
        return None
    if not (6 <= digits <= 10) or period < 1:
        return None
    return {"secret": secret, "digits": digits, "period": period, "algorithm": algorithm}


def parse_totp_field(label: str, value: str) -> dict | None:
    """Return a TOTP config {secret, digits, period, algorithm} if this field holds a 2FA seed.

    An otpauth://totp/... URI always qualifies (any label); a bare base32 seed qualifies only
    when the label contains a 2FA keyword. Returns None for everything else. Pure and total —
    never raises on user data.
    """
    if not value or not value.strip():
        return None
    value = value.strip()
    if value.lower().startswith("otpauth://"):
        return _parse_otpauth_uri(value)
    if not any(kw in label.lower() for kw in TOTP_LABEL_KEYWORDS):
        return None
    secret = _decode_base32(value)
    # Require ≥80 bits (RFC 4226's minimum secret size). This keeps short base32-valid prose
    # like "just some words" from being mistaken for a seed when guessing off a bare value.
    if not secret or len(secret) < 10:
        return None
    return {"secret": secret, "digits": 6, "period": 30, "algorithm": "sha1"}


# Password health (ROLO-0008) — all analysis runs in-process over the decrypted vault.
STRENGTH_LABELS = {0: "Empty", 1: "Weak", 2: "Fair", 3: "Good", 4: "Strong"}


def password_strength(secret: str) -> int:
    """Rate a secret 0-4 on length and character-class variety (0 empty … 4 strong).

    A lightweight heuristic — not zxcvbn — but it reliably separates short or single-class
    secrets from long, mixed ones. Anything shorter than 8 characters, or drawn from a single
    character class, is weak regardless of the other axis.
    """
    if not secret:
        return 0
    classes = sum((
        any(c.islower() for c in secret),
        any(c.isupper() for c in secret),
        any(c.isdigit() for c in secret),
        any(not c.isalnum() for c in secret),
    ))
    length = len(secret)
    if length < 8 or classes == 1:
        return 1
    if length < 12 or classes == 2:
        return 2
    if length < 16 or classes == 3:
        return 3
    return 4


def audit_passwords(vault: dict) -> list[dict]:
    """Analyse every non-empty sensitive field across the vault, worst first.

    Returns one finding per field: {entry_id, entry_name, label, strength, strength_label,
    reused, reuse_count}. `reused` is True when the same secret value appears in more than one
    sensitive field anywhere in the vault. Pure — nothing leaves the process.
    """
    counts: dict[str, int] = {}
    for entry in vault["entries"].values():
        for f in entry["fields"]:
            if f.get("sensitive") and f.get("value"):
                counts[f["value"]] = counts.get(f["value"], 0) + 1

    findings = []
    for eid, entry in vault["entries"].items():
        for f in entry["fields"]:
            value = f.get("value", "")
            if not f.get("sensitive") or not value:
                continue
            score = password_strength(value)
            reuse_count = counts.get(value, 0)
            findings.append({
                "entry_id": eid,
                "entry_name": entry["name"],
                "label": f["label"],
                "strength": score,
                "strength_label": STRENGTH_LABELS[score],
                "reused": reuse_count > 1,
                "reuse_count": reuse_count,
            })
    findings.sort(key=lambda x: (x["strength"], not x["reused"],
                                 x["entry_name"].lower(), x["label"].lower()))
    return findings


def add_entry(vault: dict, name: str, fields: list[dict], notes: str = "", category: str = "") -> str:
    entry_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    vault["entries"][entry_id] = {
        "name": name,
        "category": category,
        "fields": fields,
        "notes": notes,
        "created": now,
        "modified": now,
    }
    return entry_id


def update_entry(vault, entry_id, name=None, fields=None, notes=None, category=None):
    entry = vault["entries"][entry_id]
    if name is not None:
        entry["name"] = name
    if fields is not None:
        entry["fields"] = fields
    if notes is not None:
        entry["notes"] = notes
    if category is not None:
        entry["category"] = category
    entry["modified"] = datetime.now().isoformat()


def delete_entry(vault: dict, entry_id: str) -> None:
    del vault["entries"][entry_id]


def search_entries(vault: dict, query: str) -> list[tuple[str, dict]]:
    query_lower = query.lower()
    results = []
    for eid, entry in vault["entries"].items():
        if query_lower in entry["name"].lower():
            results.append((eid, entry))
            continue
        if entry.get("category") and query_lower in entry["category"].lower():
            results.append((eid, entry))
            continue
        matched = False
        for field in entry["fields"]:
            if query_lower in field["label"].lower() or query_lower in field["value"].lower():
                results.append((eid, entry))
                matched = True
                break
        if not matched and entry.get("notes") and query_lower in entry["notes"].lower():
            results.append((eid, entry))
    return sorted(results, key=lambda x: x[1]["name"].lower())


def list_entries(vault: dict) -> list[tuple[str, dict]]:
    return sorted(vault["entries"].items(), key=lambda x: x[1]["name"].lower())


def find_entry_by_name(vault: dict, name: str, exclude_id: str | None = None) -> str | None:
    """Return the id of an existing entry whose name matches `name` (case-insensitive,
    whitespace-trimmed), or None. `exclude_id` skips one entry so editing an entry doesn't
    flag itself as its own duplicate. Used to warn on duplicate names (ROLO-0023)."""
    target = name.strip().lower()
    for eid, entry in vault["entries"].items():
        if eid == exclude_id:
            continue
        if entry["name"].strip().lower() == target:
            return eid
    return None


def entries_noun(n: int) -> str:
    """'entry' for exactly one, else 'entries' — for count labels."""
    return "entry" if n == 1 else "entries"


# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------


def add_category(vault: dict, name: str) -> bool:
    """Add a category. Returns False if it already exists."""
    if name in vault["categories"]:
        return False
    vault["categories"].append(name)
    return True


def rename_category(vault: dict, old_name: str, new_name: str) -> None:
    idx = vault["categories"].index(old_name)
    vault["categories"][idx] = new_name
    for entry in vault["entries"].values():
        if entry.get("category") == old_name:
            entry["category"] = new_name


def delete_category(vault: dict, name: str) -> None:
    vault["categories"].remove(name)
    for entry in vault["entries"].values():
        if entry.get("category") == name:
            entry["category"] = ""


def entries_by_category(vault: dict) -> dict[str, list[tuple[str, dict]]]:
    """Return {category_name: [(eid, entry), ...]} with entries sorted by name.
    Uncategorised entries are under key ''."""
    groups: dict[str, list] = {}
    for eid, entry in vault["entries"].items():
        cat = entry.get("category", "")
        # Treat orphaned category references as uncategorised
        if cat and cat not in vault["categories"]:
            cat = ""
        groups.setdefault(cat, []).append((eid, entry))
    for lst in groups.values():
        lst.sort(key=lambda x: x[1]["name"].lower())
    return groups


# ---------------------------------------------------------------------------
# Import parser
# ---------------------------------------------------------------------------


def parse_text_file(filepath: str) -> list[dict]:
    with open(filepath, "r", encoding="utf-8") as fp:
        content = fp.read()
    blocks = re.split(r"\n\s*\n", content.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue
        name = lines[0].rstrip(":").strip()
        fields = []
        notes_lines = []
        for line in lines[1:]:
            match = re.match(r"^([^:]+?):\s+(.+)$", line)
            if match:
                label = match.group(1).strip()
                value = match.group(2).strip()
                fields.append({"label": label, "value": value, "sensitive": is_sensitive_label(label)})
            elif line.strip():
                notes_lines.append(line.strip())
        entries.append({"name": name, "fields": fields, "notes": "\n".join(notes_lines)})
    return entries


def import_entries(vault, parsed, skip_duplicates=True):
    existing_names = {e["name"].lower() for e in vault["entries"].values()}
    imported = skipped = 0
    for entry_data in parsed:
        if skip_duplicates and entry_data["name"].lower() in existing_names:
            skipped += 1
            continue
        add_entry(vault, entry_data["name"], entry_data["fields"], entry_data["notes"])
        existing_names.add(entry_data["name"].lower())
        imported += 1
    return imported, skipped


# ---------------------------------------------------------------------------
# Password generation
# ---------------------------------------------------------------------------


def generate_password(
    length: int = PW_GEN_LENGTH,
    lower: bool = True,
    upper: bool = True,
    digits: bool = True,
    symbols: bool = True,
) -> str:
    """Return a cryptographically-random password from the selected character classes.

    Uses the `secrets` module (never `random`). Every selected class is guaranteed to appear
    at least once when the length allows it, then the remainder is filled from the combined
    pool and shuffled so the guaranteed characters aren't stuck at the front.
    """
    pools = [
        PW_GEN_CLASSES[name]
        for name, wanted in (("lower", lower), ("upper", upper), ("digits", digits), ("symbols", symbols))
        if wanted
    ]
    if not pools:
        raise ValueError("at least one character class must be enabled")
    if length < 1:
        raise ValueError("length must be at least 1")

    combined = "".join(pools)
    # One char from each class first (up to length), then fill from the combined pool.
    chars = [secrets.choice(pool) for pool in pools][:length]
    chars += [secrets.choice(combined) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


def read_clipboard() -> str | None:
    """Return the current clipboard text, or None if no reader tool is available / it fails.

    Mirrors copy_to_clipboard's tool priority (Wayland first, then X11) so a read pairs with
    the writer used for the copy. Used by the auto-clear timer to only wipe the clipboard when
    its contents are still the secret we put there.
    """
    for cmd in [
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    ]:
        if shutil.which(cmd[0]):
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=5)
                if proc.returncode == 0:
                    return proc.stdout.decode("utf-8", "replace")
            except (subprocess.TimeoutExpired, OSError):
                continue
    return None


def copy_to_clipboard(text: str) -> bool:
    for cmd in [
        ["wl-copy", "--trim-newline"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]:
        if shutil.which(cmd[0]):
            try:
                proc = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=5)
                return proc.returncode == 0
            except (subprocess.TimeoutExpired, OSError):
                continue
    return False


# ===========================================================================
# ---------------------------------------------------------------------------
# Window geometry config
# ---------------------------------------------------------------------------


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(data: dict) -> None:
    try:
        existing = load_config()
        existing.update(data)
        with open(CONFIG_FILE, "w") as f:
            json.dump(existing, f)
    except OSError:
        pass


# ===========================================================================
# GTK4 / Adwaita GUI
# ===========================================================================


def clear_container(container) -> None:
    """Remove every child from a GTK container (ListBox rows, Box children, ...) (ROLO-0019)."""
    child = container.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        container.remove(child)
        child = nxt


def make_dialog_scaffold(dialog, title, *, width=None, height=None,
                         clamp_max=500, margin=16, scrolled=False):
    """Build the common Adw.Dialog shell: ToolbarView + HeaderBar + (optional scroll) + Clamp.

    Returns (header, clamp). The caller packs its own buttons into `header` and sets the body
    via clamp.set_child(...). Centralises the wiring every Adw.Dialog otherwise repeats (ROLO-0019).
    """
    dialog.set_title(title)
    if width is not None:
        dialog.set_content_width(width)
    if height is not None:
        dialog.set_content_height(height)

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    toolbar.add_top_bar(header)

    clamp = Adw.Clamp(maximum_size=clamp_max)
    clamp.set_margin_top(margin)
    clamp.set_margin_bottom(margin)
    clamp.set_margin_start(margin)
    clamp.set_margin_end(margin)

    if scrolled:
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(clamp)
        toolbar.set_content(scroll)
    else:
        toolbar.set_content(clamp)
    dialog.set_child(toolbar)
    return header, clamp


class UnlockDialog(Gtk.Window):
    """Initial password dialog - unlock existing vault or create new one."""

    def __init__(self, app, vault_path, is_new):
        super().__init__(title="Rolodex", application=app)
        self.app = app
        self.vault_path = vault_path
        self.is_new = is_new
        self.set_default_size(380, -1)
        self.set_resizable(False)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)

        # Main layout
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(header)

        clamp = Adw.Clamp(maximum_size=340)
        clamp.set_margin_top(24)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(24)
        clamp.set_margin_end(24)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Icon / title
        title = Gtk.Label(label="Rolodex")
        title.add_css_class("unlock-title")
        vbox.append(title)

        if is_new:
            sub = Gtk.Label(label="Create a master password to encrypt your vault.")
            sub.set_wrap(True)
            sub.add_css_class("dim-label")
            vbox.append(sub)

        # Password field(s) using Adw.PasswordEntryRow
        pw_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        pw_list.add_css_class("boxed-list")

        self.pw_entry = Adw.PasswordEntryRow(title="Master password")
        pw_list.append(self.pw_entry)

        if is_new:
            self.pw_confirm = Adw.PasswordEntryRow(title="Confirm password")
            pw_list.append(self.pw_confirm)

        vbox.append(pw_list)

        # Enter key support — capture phase so we see it before the entry row
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        # Status label
        self.status = Gtk.Label()
        self.status.add_css_class("error")
        self.status.set_visible(False)
        vbox.append(self.status)

        # Unlock / Create button
        btn_label = "Create Vault" if is_new else "Unlock"
        self.btn = Gtk.Button(label=btn_label)
        self.btn.add_css_class("suggested-action")
        self.btn.add_css_class("pill")
        self.btn.connect("clicked", self._on_activate)
        vbox.append(self.btn)

        clamp.set_child(vbox)
        outer.append(clamp)
        self.set_child(outer)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._on_activate()
            return True
        return False

    def _show_error(self, msg):
        self.status.set_text(msg)
        self.status.set_visible(True)

    def _on_activate(self, *_args):
        pw = self.pw_entry.get_text()
        if not pw:
            self._show_error("Please enter a password.")
            return

        if self.is_new:
            if len(pw) < MIN_PASSWORD_LENGTH:
                self._show_error(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
                return
            pw2 = self.pw_confirm.get_text()
            if pw != pw2:
                self._show_error("Passwords do not match.")
                return
            try:
                vault, salt = create_vault(pw, self.vault_path)
            except Exception as e:
                self._show_error(str(e))
                return
            self.app.open_main(vault, salt, pw, self.vault_path)
            self.close()
        else:
            self.btn.set_sensitive(False)
            self.btn.set_label("Unlocking...")
            # Run decryption in a thread so the UI doesn't freeze
            import threading
            threading.Thread(target=self._try_unlock, args=(pw,), daemon=True).start()

    def _try_unlock(self, pw):
        try:
            vault, salt = load_vault(pw, self.vault_path)
            GLib.idle_add(self._unlock_ok, vault, salt, pw)
        except InvalidToken:
            GLib.idle_add(self._unlock_fail, "Wrong password.")
        except Exception as e:
            GLib.idle_add(self._unlock_fail, str(e))

    def _unlock_ok(self, vault, salt, pw):
        migrate_vault(vault)
        self.app.open_main(vault, salt, pw, self.vault_path)
        self.close()

    def _unlock_fail(self, msg):
        self.btn.set_sensitive(True)
        self.btn.set_label("Unlock")
        self._show_error(msg)
        self.pw_entry.grab_focus()


# --------------------------------------------------------------------------
# Entry row widget for the sidebar list
# --------------------------------------------------------------------------


class EntryRow(Gtk.ListBoxRow):
    def __init__(self, entry_id: str, name: str):
        super().__init__()
        self.entry_id = entry_id
        label = Gtk.Label(label=name, xalign=0, hexpand=True)
        label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        label.set_margin_start(8)
        label.set_margin_end(8)
        self.label = label
        self.set_child(label)

        # Drag source for drag-and-drop between categories
        drag_src = Gtk.DragSource()
        drag_src.set_actions(Gdk.DragAction.MOVE)
        drag_src.connect("prepare", self._on_drag_prepare)
        drag_src.connect("drag-begin", self._on_drag_begin)
        self.add_controller(drag_src)

    def _on_drag_prepare(self, source, x, y):
        return Gdk.ContentProvider.new_for_value(self)

    def _on_drag_begin(self, source, drag):
        icon = Gtk.DragIcon.get_for_drag(drag)
        lbl = Gtk.Label(label=self.label.get_text() or "Entry")
        lbl.add_css_class("caption")
        lbl.set_margin_top(6)
        lbl.set_margin_bottom(6)
        lbl.set_margin_start(12)
        lbl.set_margin_end(12)
        icon.set_child(lbl)


# --------------------------------------------------------------------------
# Category header row for sidebar
# --------------------------------------------------------------------------


class CategoryHeaderRow(Gtk.ListBoxRow):
    """Non-selectable header row with disclosure arrow, category name, count badge."""

    def __init__(self, category_name: str, count: int, collapsed: bool):
        super().__init__()
        self.category_name = category_name
        self.set_selectable(False)
        self.set_activatable(True)
        self.add_css_class("category-header-row")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(8)
        box.set_margin_end(8)

        # Disclosure arrow
        arrow_icon = "pan-end-symbolic" if collapsed else "pan-down-symbolic"
        self.arrow = Gtk.Image(icon_name=arrow_icon)
        self.arrow.add_css_class("dim-label")
        box.append(self.arrow)

        # Category name
        display_name = category_name if category_name else "Uncategorised"
        name_label = Gtk.Label(label=display_name.upper(), xalign=0, hexpand=True)
        name_label.add_css_class("category-header-label")
        box.append(name_label)

        # Count badge
        count_label = Gtk.Label(label=str(count))
        count_label.add_css_class("category-count")
        box.append(count_label)

        self.set_child(box)

        # Drop target for dragging entries onto this category
        drop = Gtk.DropTarget(actions=Gdk.DragAction.MOVE)
        drop.set_gtypes([EntryRow])
        drop.connect("enter", self._on_drop_enter)
        drop.connect("leave", self._on_drop_leave)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

    def _on_drop_enter(self, target, x, y):
        self.add_css_class("category-drop-hover")
        return Gdk.DragAction.MOVE

    def _on_drop_leave(self, target):
        self.remove_css_class("category-drop-hover")

    def _on_drop(self, target, dragged_row, x, y):
        self.remove_css_class("category-drop-hover")
        if not isinstance(dragged_row, EntryRow):
            return False
        # Find the MainWindow ancestor
        widget = self.get_root()
        if hasattr(widget, "_move_entry_to_category"):
            widget._move_entry_to_category(dragged_row.entry_id, self.category_name)
        return True


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, vault, salt, password, vault_path):
        super().__init__(application=app, title="Rolodex")
        self.app_ref = app
        self.vault = vault
        self.salt = salt
        self.password = password
        self.vault_path = vault_path
        self._revealed = False
        # TOTP live-code tick (ROLO-0006): one 1s timer refreshes every code row on screen.
        self._totp_tick_id = None
        self._totp_widgets = []

        # Restore saved window size or use defaults
        conf = load_config()
        w = conf.get("window_width", 820)
        h = conf.get("window_height", 580)
        self.set_default_size(w, h)
        if conf.get("window_maximized"):
            self.maximize()

        # Security timeouts (0 disables either). Read once at unlock; edit .rolodex.conf to change.
        self._clipboard_clear_s = int(conf.get("clipboard_clear_seconds", DEFAULT_CLIPBOARD_CLEAR_SECONDS))
        self._idle_timeout_s = int(conf.get("idle_lock_seconds", DEFAULT_IDLE_LOCK_SECONDS))
        self._idle_source_id = None
        self._last_activity = 0

        self.connect("close-request", self._on_close_request)

        # --- Header bar with actions ---
        header = Adw.HeaderBar()

        # Left side: Add button
        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add entry (Ctrl+N)")
        add_btn.connect("clicked", self._on_add)
        header.pack_start(add_btn)

        # Right side: menu
        menu = Gio.Menu()
        menu.append("Password health...", "win.health")
        menu.append("Manage categories...", "win.manage-categories")
        menu.append("Import from text file...", "win.import")
        menu.append("Backup vault...", "win.backup")
        menu.append("Restore vault from backup...", "win.restore")
        menu.append("Export (decrypted plaintext)...", "win.export")
        menu.append("Change master password...", "win.chpass")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(menu_btn)

        # Manual Lock button (ROLO-0002), also on Ctrl+L.
        lock_btn = Gtk.Button(icon_name="changes-prevent-symbolic", tooltip_text="Lock vault (Ctrl+L)")
        lock_btn.connect("clicked", self._lock)
        header.pack_end(lock_btn)

        # Actions
        for name, callback in [
            ("health", self._on_password_health),
            ("manage-categories", self._on_manage_categories),
            ("import", self._on_import),
            ("backup", self._on_backup),
            ("restore", self._on_restore),
            ("export", self._on_export),
            ("chpass", self._on_change_password),
        ]:
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            self.add_action(action)

        # "Move to category" action for right-click context menu
        move_action = Gio.SimpleAction(name="move-to-category", parameter_type=GLib.VariantType.new("(ss)"))
        move_action.connect("activate", self._on_move_to_category_action)
        self.add_action(move_action)

        # Lock action + Ctrl+L accelerator (ROLO-0002).
        lock_action = Gio.SimpleAction(name="lock")
        lock_action.connect("activate", self._lock)
        self.add_action(lock_action)
        app.set_accels_for_action("win.lock", ["<Control>l"])

        # Keyboard shortcuts for common actions (ROLO-0007). Ctrl+Shift+C copies the
        # selected entry's secret while plain Ctrl+C stays free for copying selected text.
        for name, callback, accels in [
            ("focus-search", self._focus_search, ["<Control>f"]),
            ("add", self._on_add, ["<Control>n"]),
            ("copy-secret", self._copy_secret, ["<Control><Shift>c"]),
            ("shortcuts", self._show_shortcuts, ["<Control>question"]),
        ]:
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            self.add_action(action)
            app.set_accels_for_action(f"win.{name}", accels)

        # --- Paned: sidebar | detail ---
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.add_css_class("main-paned")
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        paned.set_position(260)

        # ---- Left sidebar ----
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left_box.add_css_class("sidebar-box")

        # Search
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search entries...")
        self.search_entry.set_margin_top(8)
        self.search_entry.set_margin_start(8)
        self.search_entry.set_margin_end(8)
        self.search_entry.set_margin_bottom(4)
        self.search_entry.connect("search-changed", self._on_search_changed)
        # Escape clears the search box (ROLO-0007); scoped to the entry so it never
        # shadows the dialog/popover Escape handling elsewhere.
        self.search_entry.connect("stop-search", lambda e: e.set_text(""))
        left_box.append(self.search_entry)

        # Count label
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("count-label")
        self.count_label.add_css_class("caption")
        self.count_label.set_margin_start(12)
        self.count_label.set_margin_bottom(4)
        left_box.append(self.count_label)

        # List box in a scrolled window
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-selected", self._on_row_selected)
        self.listbox.connect("row-activated", self._on_row_activated)
        scroll.set_child(self.listbox)
        left_box.append(scroll)

        paned.set_start_child(left_box)

        # ---- Right detail pane ----
        self.detail_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.detail_scroll.set_child(self.detail_box)

        # Placeholder when nothing selected
        self.placeholder = Adw.StatusPage(
            title="Select an entry",
            description="Choose an entry from the list, or add a new one.",
            icon_name="contact-new-symbolic",
        )
        self.placeholder.set_vexpand(True)

        # Stack: placeholder vs detail
        self.detail_stack = Gtk.Stack()
        self.detail_stack.add_named(self.placeholder, "empty")
        self.detail_stack.add_named(self.detail_scroll, "detail")
        self.detail_stack.set_visible_child_name("empty")

        paned.set_end_child(self.detail_stack)

        # --- Assemble with toast overlay ---
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.append(header)
        main_box.append(paned)
        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(main_box)
        self.set_content(self._toast_overlay)

        self._current_entry_id = None
        self._collapsed_categories: set[str] = set()
        self._search_debounce_id = 0  # pending GLib timeout for debounced search (ROLO-0018)
        migrate_vault(self.vault)
        self._refresh_list()

        # Auto-lock on idle (ROLO-0002): any pointer motion or key press resets the activity
        # clock; a periodic check locks the vault once the idle timeout is exceeded.
        self._last_activity = GLib.get_monotonic_time()
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._bump_activity)
        self.add_controller(motion)
        keyctl = Gtk.EventControllerKey()
        keyctl.connect("key-pressed", self._bump_activity)
        self.add_controller(keyctl)
        self._start_idle_timer()

    # ------------------------------------------------------------------
    # Vault persistence
    # ------------------------------------------------------------------

    def _save(self):
        save_vault(self.vault, self.password, self.salt, self.vault_path)

    def _on_close_request(self, *_args):
        self._cancel_search_debounce()
        self._cancel_totp_tick()  # covers _lock too, which routes through close()
        save_config({
            "window_width": self.get_width(),
            "window_height": self.get_height(),
            "window_maximized": self.is_maximized(),
        })
        return False  # allow the window to close

    # ------------------------------------------------------------------
    # Sidebar list
    # ------------------------------------------------------------------

    def _refresh_list(self, select_id=None):
        query = self.search_entry.get_text().strip()
        categories = self.vault.get("categories", [])

        # Clear list
        clear_container(self.listbox)

        select_row = None
        total = len(self.vault["entries"])

        if query:
            # Search active: flat list, no grouping
            entries = search_entries(self.vault, query)
            for eid, entry in entries:
                row = EntryRow(eid, entry["name"])
                self._attach_entry_context_menu(row)
                self.listbox.append(row)
                if eid == select_id:
                    select_row = row
            self.count_label.set_text(f"{len(entries)} of {total} {entries_noun(total)}")

        elif categories:
            # Grouped view
            groups = entries_by_category(self.vault)
            shown = 0
            for cat_name in categories:
                cat_entries = groups.get(cat_name, [])
                collapsed = cat_name in self._collapsed_categories
                header = CategoryHeaderRow(cat_name, len(cat_entries), collapsed)
                self.listbox.append(header)
                if not collapsed:
                    for eid, entry in cat_entries:
                        row = EntryRow(eid, entry["name"])
                        self._attach_entry_context_menu(row)
                        self.listbox.append(row)
                        if eid == select_id:
                            select_row = row
                shown += len(cat_entries)

            # Uncategorised last
            uncat = groups.get("", [])
            if uncat:
                collapsed = "" in self._collapsed_categories
                header = CategoryHeaderRow("", len(uncat), collapsed)
                self.listbox.append(header)
                if not collapsed:
                    for eid, entry in uncat:
                        row = EntryRow(eid, entry["name"])
                        self._attach_entry_context_menu(row)
                        self.listbox.append(row)
                        if eid == select_id:
                            select_row = row
                shown += len(uncat)

            self.count_label.set_text(f"{total} {entries_noun(total)}")

        else:
            # No categories: flat list (backward-compatible)
            entries = list_entries(self.vault)
            for eid, entry in entries:
                row = EntryRow(eid, entry["name"])
                self._attach_entry_context_menu(row)
                self.listbox.append(row)
                if eid == select_id:
                    select_row = row
            self.count_label.set_text(f"{total} {entries_noun(total)}")

        if select_row:
            self.listbox.select_row(select_row)
        elif self._current_entry_id:
            # Try to re-select current entry
            idx = 0
            while True:
                row = self.listbox.get_row_at_index(idx)
                if row is None:
                    break
                if isinstance(row, EntryRow) and row.entry_id == self._current_entry_id:
                    self.listbox.select_row(row)
                    return
                idx += 1
            # Entry gone or collapsed, clear detail
            self._current_entry_id = None
            self.detail_stack.set_visible_child_name("empty")

    def _on_search_changed(self, entry):
        # Debounce (ROLO-0018): restart a short timer on each keystroke so the (relatively
        # expensive) full rebuild runs once the user pauses, not per character.
        self._cancel_search_debounce()
        self._search_debounce_id = GLib.timeout_add(SEARCH_DEBOUNCE_MS, self._apply_search)

    def _cancel_search_debounce(self):
        if self._search_debounce_id:
            GLib.source_remove(self._search_debounce_id)
            self._search_debounce_id = 0

    def _apply_search(self):
        self._search_debounce_id = 0
        if self.vault is not None:  # guard against a timer firing after lock/close
            self._refresh_list()
        return GLib.SOURCE_REMOVE

    def _on_row_selected(self, listbox, row):
        if row is None:
            self._current_entry_id = None
            self.detail_stack.set_visible_child_name("empty")
            return
        if isinstance(row, CategoryHeaderRow):
            return
        self._current_entry_id = row.entry_id
        self._revealed = False
        self._show_detail(row.entry_id)

    def _on_row_activated(self, listbox, row):
        if isinstance(row, CategoryHeaderRow):
            cat = row.category_name
            if cat in self._collapsed_categories:
                self._collapsed_categories.discard(cat)
            else:
                self._collapsed_categories.add(cat)
            self._refresh_list()

    # ------------------------------------------------------------------
    # Detail pane
    # ------------------------------------------------------------------

    def _show_detail(self, entry_id):
        self._cancel_totp_tick()  # stop any prior entry's live-code timer before rebuilding
        if entry_id not in self.vault["entries"]:
            self.detail_stack.set_visible_child_name("empty")
            return
        entry = self.vault["entries"][entry_id]
        self.detail_stack.set_visible_child_name("detail")

        # Clear old contents
        clear_container(self.detail_box)

        clamp = Adw.Clamp(maximum_size=560)
        clamp.set_margin_top(20)
        clamp.set_margin_bottom(20)
        clamp.set_margin_start(20)
        clamp.set_margin_end(20)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Entry name header
        name_label = Gtk.Label(label=entry["name"], xalign=0)
        name_label.add_css_class("title-2")
        name_label.add_css_class("entry-title")
        name_label.set_selectable(True)
        name_label.set_wrap(True)
        vbox.append(name_label)

        # Separator
        vbox.append(Gtk.Separator())

        # Fields in an Adw.PreferencesGroup style
        fields_group = Gtk.ListBox()
        fields_group.set_selection_mode(Gtk.SelectionMode.NONE)
        fields_group.add_css_class("boxed-list")

        for i, field in enumerate(entry["fields"]):
            row = Adw.ActionRow()
            row.set_title(GLib.markup_escape_text(field["label"]))
            row.add_css_class(f"field-{field_category(field['label'])}")

            # Value display
            if field.get("sensitive") and not self._revealed:
                display = MASK
            else:
                display = field["value"]

            val_label = Gtk.Label(label=display)
            val_label.set_selectable(True)
            if field.get("sensitive") and not self._revealed:
                val_label.add_css_class("field-masked")
            elif field.get("sensitive") and self._revealed:
                val_label.add_css_class("field-revealed-sensitive")
            row.add_suffix(val_label)

            # Copy button
            copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                                  tooltip_text=f"Copy {field['label']}")
            copy_btn.add_css_class("flat")
            copy_btn.add_css_class("copy-btn")
            copy_btn.connect("clicked", self._make_copy_handler(field["value"], field["label"]))
            row.add_suffix(copy_btn)

            fields_group.append(row)

            # ROLO-0006: a 2FA seed gets a live-code row right beneath it.
            totp_cfg = parse_totp_field(field["label"], field["value"])
            if totp_cfg:
                fields_group.append(self._build_totp_row(totp_cfg))

        vbox.append(fields_group)

        # Notes
        if entry.get("notes"):
            notes_label_header = Gtk.Label(label="Notes", xalign=0)
            notes_label_header.add_css_class("heading")
            notes_label_header.set_margin_top(8)
            vbox.append(notes_label_header)

            notes_frame = Gtk.Frame()
            notes_frame.add_css_class("notes-frame")
            notes_text = Gtk.Label(label=entry["notes"], xalign=0, selectable=True, wrap=True)
            notes_text.set_margin_top(8)
            notes_text.set_margin_bottom(8)
            notes_text.set_margin_start(12)
            notes_text.set_margin_end(12)
            notes_frame.set_child(notes_text)
            vbox.append(notes_frame)

        # Action buttons row
        btn_box = Gtk.Box(spacing=8, margin_top=12)
        btn_box.set_halign(Gtk.Align.START)

        toggle_text = "Hide sensitive" if self._revealed else "Reveal sensitive"
        reveal_btn = Gtk.Button(label=toggle_text)
        reveal_btn.add_css_class("reveal-btn")
        reveal_btn.connect("clicked", self._on_toggle_reveal, entry_id)
        btn_box.append(reveal_btn)

        edit_btn = Gtk.Button(label="Edit")
        edit_btn.add_css_class("edit-btn")
        edit_btn.connect("clicked", self._on_edit, entry_id)
        btn_box.append(edit_btn)

        delete_btn = Gtk.Button(label="Delete")
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect("clicked", self._on_delete, entry_id)
        btn_box.append(delete_btn)

        vbox.append(btn_box)

        # Timestamps
        ts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=16)
        created = entry.get("created", "")[:19].replace("T", " ")
        modified = entry.get("modified", "")[:19].replace("T", " ")
        ts_created = Gtk.Label(label=f"Created: {created}", xalign=0)
        ts_created.add_css_class("timestamp")
        ts_modified = Gtk.Label(label=f"Modified: {modified}", xalign=0)
        ts_modified.add_css_class("timestamp")
        ts_box.append(ts_created)
        ts_box.append(ts_modified)
        vbox.append(ts_box)

        clamp.set_child(vbox)
        self.detail_box.append(clamp)

        # Start the shared 1s ticker only if this entry actually shows a code. The first tick
        # runs now so codes appear immediately rather than after a blank second.
        if self._totp_widgets:
            self._totp_tick()
            self._totp_tick_id = GLib.timeout_add_seconds(1, self._totp_tick)

    def _cancel_totp_tick(self):
        """Stop the live-code timer and drop the tracked rows (called on every rebuild/close)."""
        if self._totp_tick_id is not None:
            GLib.source_remove(self._totp_tick_id)
            self._totp_tick_id = None
        self._totp_widgets = []

    def _build_totp_row(self, cfg):
        """A 'Code' row: grouped live digits, a depleting ring, seconds left, and copy."""
        state = {"code": "", "fraction": 1.0}
        row = Adw.ActionRow()
        row.set_title("Code")
        row.add_css_class("totp-row")

        code_label = Gtk.Label(valign=Gtk.Align.CENTER, selectable=True)
        code_label.add_css_class("totp-code")
        row.add_suffix(code_label)

        ring = Gtk.DrawingArea(valign=Gtk.Align.CENTER)
        ring.set_content_width(18)
        ring.set_content_height(18)
        ring.set_draw_func(self._draw_totp_ring, state)
        row.add_suffix(ring)

        rem_label = Gtk.Label(valign=Gtk.Align.CENTER)
        rem_label.add_css_class("totp-remaining")
        row.add_suffix(rem_label)

        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text="Copy 2FA code")
        copy_btn.add_css_class("flat")
        copy_btn.add_css_class("copy-btn")
        copy_btn.connect("clicked", lambda _b: self._copy_value(state["code"], "2FA code"))
        row.add_suffix(copy_btn)

        self._totp_widgets.append({
            "cfg": cfg, "state": state, "code_label": code_label,
            "ring": ring, "rem_label": rem_label,
        })
        return row

    def _totp_tick(self):
        """Recompute the code + remaining window for every visible code row, once per second."""
        now = time.time()
        for w in self._totp_widgets:
            cfg = w["cfg"]
            code = totp_code(cfg["secret"], now, cfg["digits"], cfg["period"], cfg["algorithm"])
            rem = totp_remaining(now, cfg["period"])
            w["state"]["code"] = code
            w["state"]["fraction"] = rem / cfg["period"]
            mid = len(code) // 2  # group as two halves for readability (492 831 / 4920 8317)
            w["code_label"].set_text(f"{code[:mid]} {code[mid:]}")
            w["rem_label"].set_text(f"{rem}s")
            w["ring"].queue_draw()
        return True  # repeat; cancelled explicitly via _cancel_totp_tick

    def _draw_totp_ring(self, _area, cr, width, height, state):
        """Draw a ring that empties clockwise from the top as the code's window elapses."""
        frac = state.get("fraction", 1.0)
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 2
        cr.set_line_width(2.5)
        cr.set_source_rgba(1, 1, 1, 0.15)  # faint full-circle track
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgba(0.36, 0.66, 1.0, 0.95)  # remaining arc, in the accent blue
        start = -math.pi / 2
        cr.arc(cx, cy, radius, start, start + frac * 2 * math.pi)
        cr.stroke()

    def _copy_value(self, value, label):
        """Copy a secret to the clipboard with the auto-clear timer + toast (ROLO-0003)."""
        if not copy_to_clipboard(value):
            self._toast("Clipboard not available")
            return
        delay = self._clipboard_clear_s
        if delay > 0:
            self._toast(f"Copied {label} — clipboard clears in {delay}s")
            GLib.timeout_add_seconds(delay, self._clear_clipboard_if_unchanged, value)
        else:
            self._toast(f"Copied {label}")

    def _make_copy_handler(self, value, label):
        return lambda _btn: self._copy_value(value, label)

    def _clear_clipboard_if_unchanged(self, value):
        """Wipe the clipboard, but only if it still holds the secret we copied (ROLO-0003)."""
        current = read_clipboard()
        # If a reader is available and the clipboard has moved on, leave the user's new copy alone.
        if current is not None and current != value:
            return False
        copy_to_clipboard("")
        return False  # one-shot timeout

    def _toast(self, msg):
        self._toast_overlay.add_toast(Adw.Toast(title=msg, timeout=2))

    # ------------------------------------------------------------------
    # Keyboard shortcuts (ROLO-0007)
    # ------------------------------------------------------------------

    def _focus_search(self, *_args):
        self.search_entry.grab_focus()

    def _copy_secret(self, *_args):
        """Copy the selected entry's first sensitive field (Ctrl+Shift+C)."""
        entry_id = self._current_entry_id
        if not entry_id or entry_id not in self.vault["entries"]:
            self._toast("Select an entry first")
            return
        field = next((f for f in self.vault["entries"][entry_id]["fields"]
                      if f.get("sensitive")), None)
        if field is None:
            self._toast("No sensitive field to copy")
            return
        self._copy_value(field["value"], field["label"])

    def _show_shortcuts(self, *_args):
        ShortcutsDialog().present(self)

    def _on_password_health(self, *_args):
        PasswordHealthDialog(self).present(self)

    # ------------------------------------------------------------------
    # Auto-lock (ROLO-0002)
    # ------------------------------------------------------------------

    def _bump_activity(self, *_args):
        self._last_activity = GLib.get_monotonic_time()
        return False  # never swallow the event

    def _start_idle_timer(self):
        if self._idle_source_id is not None:
            GLib.source_remove(self._idle_source_id)
            self._idle_source_id = None
        if self._idle_timeout_s <= 0:
            return
        # Check a handful of times within the window; no need to poll every second.
        interval = max(5, min(30, self._idle_timeout_s))
        self._idle_source_id = GLib.timeout_add_seconds(interval, self._idle_check)

    def _idle_check(self):
        if self._idle_timeout_s <= 0 or self.vault is None:
            self._idle_source_id = None
            return False
        idle_us = GLib.get_monotonic_time() - self._last_activity
        if idle_us >= self._idle_timeout_s * 1_000_000:
            self._idle_source_id = None  # this source is removed by the False return below
            self._lock()
            return False
        return True

    def _lock(self, *_args):
        """Discard the decrypted vault + master password and return to the unlock screen."""
        if self._idle_source_id is not None:
            GLib.source_remove(self._idle_source_id)
            self._idle_source_id = None
        self._cancel_search_debounce()
        # Wipe secrets from memory before showing the lock screen. Every mutation already saves
        # via _save(), so there is nothing unsaved to lose here.
        self.vault = None
        self.salt = None
        self.password = None
        app, path = self.app_ref, self.vault_path
        self.close()
        UnlockDialog(app, path, is_new=False).present()

    def _on_toggle_reveal(self, btn, entry_id):
        self._revealed = not self._revealed
        self._show_detail(entry_id)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _on_delete(self, btn, entry_id):
        entry = self.vault["entries"][entry_id]
        dialog = Adw.AlertDialog(
            heading="Delete entry?",
            body=f'Delete "{entry["name"]}"? This cannot be undone.',
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_response, entry_id)
        dialog.present(self)

    def _on_delete_response(self, dialog, response, entry_id):
        if response == "delete":
            delete_entry(self.vault, entry_id)
            self._save()
            self._current_entry_id = None
            self.detail_stack.set_visible_child_name("empty")
            self._refresh_list()
            self._toast("Entry deleted")

    # ------------------------------------------------------------------
    # Add entry dialog
    # ------------------------------------------------------------------

    def _on_add(self, *_args):
        dialog = AddEditDialog(self, "Add Entry")
        dialog.present(self)

    def _finish_add(self, name, fields, notes, category=""):
        eid = add_entry(self.vault, name, fields, notes, category=category)
        self._save()
        self._refresh_list(select_id=eid)
        self._toast(f'Added "{name}"')

    # ------------------------------------------------------------------
    # Edit entry dialog
    # ------------------------------------------------------------------

    def _on_edit(self, btn, entry_id):
        entry = self.vault["entries"][entry_id]
        dialog = AddEditDialog(self, "Edit Entry", entry_id=entry_id, entry=entry)
        dialog.present(self)

    def _finish_edit(self, entry_id, name, fields, notes, category=""):
        update_entry(self.vault, entry_id, name=name, fields=fields, notes=notes, category=category)
        self._save()
        self._refresh_list(select_id=entry_id)
        self._show_detail(entry_id)
        self._toast("Entry updated")

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _on_import(self, *_args):
        chooser = Gtk.FileDialog()
        chooser.set_title("Import from text file")
        txt_filter = Gtk.FileFilter()
        txt_filter.set_name("Text files")
        txt_filter.add_mime_type("text/plain")
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(txt_filter)
        filters.append(all_filter)
        chooser.set_filters(filters)

        # Start the picker in the user's home directory
        home = GLib.get_home_dir()
        if home:
            chooser.set_initial_folder(Gio.File.new_for_path(home))

        chooser.open(self, None, self._on_import_file_chosen)

    def _on_import_file_chosen(self, chooser, result):
        try:
            gfile = chooser.open_finish(result)
        except GLib.Error:
            return
        filepath = gfile.get_path()
        if not filepath:
            return

        try:
            parsed = parse_text_file(filepath)
        except Exception as e:
            self._show_message("Import Error", str(e))
            return

        if not parsed:
            self._show_message("Import", "No entries found in file.")
            return

        # Show preview dialog
        dialog = ImportPreviewDialog(self, parsed, filepath)
        dialog.present(self)

    def _finish_import(self, parsed):
        imported, skipped = import_entries(self.vault, parsed)
        self._save()
        self._refresh_list()
        msg = f"Imported {imported} entries."
        if skipped:
            msg += f" Skipped {skipped} duplicates."
        self._toast(msg)

    # ------------------------------------------------------------------
    # Backup (encrypted copy)
    # ------------------------------------------------------------------

    def _on_backup(self, *_args):
        # Save latest state first
        self._save()

        save_dialog = Gtk.FileDialog()
        save_dialog.set_title("Backup vault to...")
        default_name = f"contacts_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.vault"
        save_dialog.set_initial_name(default_name)
        save_dialog.save(self, None, self._on_backup_file_chosen)

    def _on_backup_file_chosen(self, chooser, result):
        try:
            gfile = chooser.save_finish(result)
        except GLib.Error:
            return
        filepath = gfile.get_path()
        if not filepath:
            return
        try:
            shutil.copy2(self.vault_path, filepath)
            os.chmod(filepath, 0o600)
            self._toast("Vault backed up")
        except Exception as e:
            self._show_message("Backup Error", str(e))

    # ------------------------------------------------------------------
    # Restore (from encrypted backup)
    # ------------------------------------------------------------------

    def _on_restore(self, *_args):
        dialog = Adw.AlertDialog(
            heading="Restore from backup",
            body="This will replace all current entries with the backup contents. You will need to enter the backup's master password.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_restore_confirmed)
        dialog.present(self)

    def _on_restore_confirmed(self, dialog, response):
        if response != "restore":
            return
        chooser = Gtk.FileDialog()
        chooser.set_title("Select vault backup")
        vault_filter = Gtk.FileFilter()
        vault_filter.set_name("Vault files")
        vault_filter.add_pattern("*.vault")
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(vault_filter)
        filters.append(all_filter)
        chooser.set_filters(filters)
        chooser.open(self, None, self._on_restore_file_chosen)

    def _on_restore_file_chosen(self, chooser, result):
        try:
            gfile = chooser.open_finish(result)
        except GLib.Error:
            return
        filepath = gfile.get_path()
        if not filepath:
            return
        # Prompt for the backup's master password
        self._restore_path = filepath
        pw_dialog = RestorePasswordDialog(self)
        pw_dialog.present(self)

    def _finish_restore(self, vault, salt, password):
        migrate_vault(vault)
        self.vault = vault
        self.salt = salt
        self.password = password
        self._save()
        self._current_entry_id = None
        self.detail_stack.set_visible_child_name("empty")
        self._refresh_list()
        count = len(self.vault["entries"])
        self._toast(f"Restored {count} entries from backup")

    # ------------------------------------------------------------------
    # Export (decrypted plaintext)
    # ------------------------------------------------------------------

    def _on_export(self, *_args):
        dialog = Adw.AlertDialog(
            heading="Export decrypted backup",
            body="This will export all entries in plaintext. Continue?",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("export", "Export")
        dialog.set_response_appearance("export", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_export_confirmed)
        dialog.present(self)

    def _on_export_confirmed(self, dialog, response):
        if response != "export":
            return

        save_dialog = Gtk.FileDialog()
        save_dialog.set_title("Export to file")
        default_name = f"rolodex_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        save_dialog.set_initial_name(default_name)
        save_dialog.save(self, None, self._on_export_file_chosen)

    def _on_export_file_chosen(self, chooser, result):
        try:
            gfile = chooser.save_finish(result)
        except GLib.Error:
            return
        filepath = gfile.get_path()
        if not filepath:
            return

        entries = list_entries(self.vault)
        lines = []
        for eid, entry in entries:
            lines.append(entry["name"])
            if entry.get("category"):
                lines.append(f"  Category: {entry['category']}")
            max_label = max((len(f["label"]) for f in entry["fields"]), default=0)
            for field in entry["fields"]:
                label = field["label"].ljust(max_label)
                lines.append(f"  {label}  {field['value']}")
            if entry.get("notes"):
                lines.append(f"  Notes: {entry['notes']}")
            lines.append("")

        content = "\n".join(lines)
        write_private_file(filepath, content.encode("utf-8"))

        self._toast(f"Exported {len(entries)} entries")

    # ------------------------------------------------------------------
    # Change password
    # ------------------------------------------------------------------

    def _on_change_password(self, *_args):
        dialog = ChangePasswordDialog(self)
        dialog.present(self)

    def _finish_change_password(self, new_pw):
        self.password = new_pw
        self.salt = os.urandom(16)
        self._save()
        self._toast("Master password changed")

    # ------------------------------------------------------------------
    # Category management
    # ------------------------------------------------------------------

    def _on_manage_categories(self, *_args):
        dialog = ManageCategoriesDialog(self)
        dialog.present(self)

    def _move_entry_to_category(self, entry_id, category):
        """Move an entry to a category ('' = Uncategorised). Saves vault."""
        if entry_id in self.vault["entries"]:
            self.vault["entries"][entry_id]["category"] = category
            self.vault["entries"][entry_id]["modified"] = datetime.now().isoformat()
            self._save()
            self._refresh_list()
            if self._current_entry_id == entry_id:
                self._show_detail(entry_id)

    def _on_move_to_category_action(self, action, param):
        entry_id, category = param.unpack()
        self._move_entry_to_category(entry_id, category)

    def _attach_entry_context_menu(self, entry_row):
        """Attach a right-click context menu with 'Move to...' to an EntryRow."""
        categories = self.vault.get("categories", [])
        if not categories:
            return
        gesture = Gtk.GestureClick(button=3)
        gesture.connect("pressed", self._on_entry_right_click, entry_row)
        entry_row.add_controller(gesture)

    def _on_entry_right_click(self, gesture, n_press, x, y, entry_row):
        categories = self.vault.get("categories", [])
        if not categories:
            return
        entry = self.vault["entries"].get(entry_row.entry_id)
        if not entry:
            return
        current_cat = entry.get("category", "")

        popover = Gtk.Popover()
        popover.set_parent(entry_row)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.set_has_arrow(False)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Gtk.Label(label="Move to...", xalign=0)
        header.add_css_class("heading")
        header.set_margin_top(6)
        header.set_margin_bottom(4)
        header.set_margin_start(8)
        header.set_margin_end(8)
        vbox.append(header)
        vbox.append(Gtk.Separator())

        def make_move_handler(eid, cat, pop):
            def handler(_btn):
                pop.popdown()
                self._move_entry_to_category(eid, cat)
            return handler

        if current_cat != "":
            btn = Gtk.Button(label="Uncategorised")
            btn.add_css_class("flat")
            btn.connect("clicked", make_move_handler(entry_row.entry_id, "", popover))
            vbox.append(btn)
        for cat in categories:
            if cat != current_cat:
                btn = Gtk.Button(label=cat)
                btn.add_css_class("flat")
                btn.connect("clicked", make_move_handler(entry_row.entry_id, cat, popover))
                vbox.append(btn)

        popover.set_child(vbox)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _show_message(self, title, body):
        d = Adw.AlertDialog(heading=title, body=body)
        d.add_response("ok", "OK")
        d.present(self)


# --------------------------------------------------------------------------
# Add/Edit entry dialog
# --------------------------------------------------------------------------


class FieldRow(Gtk.ListBoxRow):
    """A single draggable field row inside the Add/Edit dialog."""

    def __init__(self, dialog, label="", value="", sensitive=None):
        super().__init__()
        self.dialog = dialog

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(4)
        box.set_margin_end(4)

        # Drag handle
        handle = Gtk.Image(icon_name="list-drag-handle-symbolic")
        handle.add_css_class("dim-label")
        handle.set_tooltip_text("Drag to reorder")
        box.append(handle)

        self.label_entry = Gtk.Entry(placeholder_text="Label", text=label, hexpand=True)
        self.label_entry.set_size_request(110, -1)
        box.append(self.label_entry)

        self.value_entry = Gtk.Entry(placeholder_text="Value", text=value, hexpand=True)
        self.value_entry.set_size_request(160, -1)
        box.append(self.value_entry)

        if sensitive is None:
            sensitive = is_sensitive_label(label)

        # Password generator (ROLO-0004): only offered on sensitive fields, since generating a
        # strong secret only makes sense for passwords/keys.
        self.gen_btn = Gtk.MenuButton(icon_name="view-refresh-symbolic",
                                      tooltip_text="Generate a strong password")
        self.gen_btn.add_css_class("flat")
        self.gen_btn.set_popover(self._build_generator_popover())
        self.gen_btn.set_visible(sensitive)
        box.append(self.gen_btn)

        self.sens_check = Gtk.CheckButton(label="Hide", active=sensitive)
        box.append(self.sens_check)

        # Peek toggle (ROLO-0021): sensitive values render masked, with an eye icon inside
        # the value box to reveal/hide them while editing. The peek is view-only — it never
        # changes the "Hide" flag that decides how the field is stored.
        self._peek = False
        self.value_entry.connect("icon-press", self._on_value_icon_press)
        self._update_value_visibility()

        # The "Hide" checkbox decides whether the value is a secret. Toggling it resets any
        # peek and shows/hides the generator button (generating only makes sense for secrets).
        def on_sens_toggled(check):
            self._peek = False
            self.gen_btn.set_visible(check.get_active())
            self._update_value_visibility()
        self.sens_check.connect("toggled", on_sens_toggled)

        # Auto-check "Hide" when the label gains a sensitive keyword (one-way; the user can
        # un-check manually). Removing the keyword leaves the checkbox as-is.
        def on_label_changed(entry):
            if is_sensitive_label(entry.get_text()):
                self.sens_check.set_active(True)
        self.label_entry.connect("changed", on_label_changed)

        remove_btn = Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text="Remove field")
        remove_btn.add_css_class("flat")
        remove_btn.add_css_class("error")
        remove_btn.connect("clicked", lambda b: self.dialog._remove_field_row(self))
        box.append(remove_btn)

        self.set_child(box)

        # --- Drag source (on the handle) ---
        drag_src = Gtk.DragSource()
        drag_src.set_actions(Gdk.DragAction.MOVE)
        drag_src.connect("prepare", self._on_drag_prepare)
        drag_src.connect("drag-begin", self._on_drag_begin)
        handle.add_controller(drag_src)

        # --- Drop target (on the whole row) ---
        drop = Gtk.DropTarget(actions=Gdk.DragAction.MOVE)
        drop.set_gtypes([FieldRow])
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

    def _update_value_visibility(self):
        """Mask/reveal the value and drive the eye icon. A sensitive field is masked unless
        the user is peeking; the icon appears only on sensitive fields and reflects state."""
        sensitive = self.sens_check.get_active()
        self.value_entry.set_visibility(not sensitive or self._peek)
        pos = Gtk.EntryIconPosition.SECONDARY
        if sensitive:
            self.value_entry.set_icon_from_icon_name(
                pos, "view-conceal-symbolic" if self._peek else "view-reveal-symbolic")
            self.value_entry.set_icon_activatable(pos, True)
            self.value_entry.set_icon_tooltip_text(
                pos, "Hide value" if self._peek else "Show value")
        else:
            self.value_entry.set_icon_from_icon_name(pos, None)

    def _on_value_icon_press(self, _entry, icon_pos):
        if icon_pos == Gtk.EntryIconPosition.SECONDARY:
            self._peek = not self._peek
            self._update_value_visibility()

    def _build_generator_popover(self) -> Gtk.Popover:
        """A small popover with length + character-class options and a Generate button."""
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)

        len_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        len_row.append(Gtk.Label(label="Length", xalign=0, hexpand=True))
        adj = Gtk.Adjustment(value=PW_GEN_LENGTH, lower=MIN_PASSWORD_LENGTH, upper=128,
                             step_increment=1, page_increment=4)
        length_spin = Gtk.SpinButton(adjustment=adj, numeric=True)
        len_row.append(length_spin)
        box.append(len_row)

        checks = {}
        for key, lbl in (("lower", "Lowercase (a–z)"), ("upper", "Uppercase (A–Z)"),
                         ("digits", "Digits (0–9)"), ("symbols", "Symbols (!@#…)")):
            check = Gtk.CheckButton(label=lbl, active=True)
            checks[key] = check
            box.append(check)

        gen = Gtk.Button(label="Generate")
        gen.add_css_class("suggested-action")
        box.append(gen)

        def do_generate(_btn):
            opts = {k: c.get_active() for k, c in checks.items()}
            pw = generate_password(length=int(length_spin.get_value()), **opts)
            self.value_entry.set_text(pw)
            self.sens_check.set_active(True)  # a generated value is a secret — save it masked
            pop.popdown()
        gen.connect("clicked", do_generate)

        # Can't generate with no character class selected — disable the button instead.
        def sync_gen_sensitive(*_a):
            gen.set_sensitive(any(c.get_active() for c in checks.values()))
        for c in checks.values():
            c.connect("toggled", sync_gen_sensitive)

        pop.set_child(box)
        return pop

    def _on_drag_prepare(self, source, x, y):
        return Gdk.ContentProvider.new_for_value(self)

    def _on_drag_begin(self, source, drag):
        icon = Gtk.DragIcon.get_for_drag(drag)
        lbl = Gtk.Label(label=self.label_entry.get_text() or "Field")
        lbl.add_css_class("caption")
        lbl.set_margin_top(6)
        lbl.set_margin_bottom(6)
        lbl.set_margin_start(12)
        lbl.set_margin_end(12)
        icon.set_child(lbl)

    def _on_drop(self, target, dragged_row, x, y):
        if dragged_row is self:
            return False
        self.dialog._reorder_field(dragged_row, self)
        return True


class AddEditDialog(Adw.Dialog):
    def __init__(self, main_win, title, entry_id=None, entry=None):
        super().__init__()
        self.main_win = main_win
        self.entry_id = entry_id

        header, clamp = make_dialog_scaffold(
            self, title, width=520, height=560, clamp_max=500, margin=16, scrolled=True)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Name
        name_group = Adw.PreferencesGroup(title="Name")
        self.name_entry = Adw.EntryRow(title="System / service name")
        if entry:
            self.name_entry.set_text(entry["name"])
        name_group.add(self.name_entry)
        vbox.append(name_group)

        # Category dropdown
        categories = main_win.vault.get("categories", [])
        if categories:
            cat_group = Adw.PreferencesGroup(title="Category")
            cat_items = ["(None)"] + categories
            string_list = Gtk.StringList.new(cat_items)
            self.category_row = Adw.ComboRow(title="Category", model=string_list)
            # Pre-select current category
            if entry and entry.get("category"):
                try:
                    sel_idx = cat_items.index(entry["category"])
                    self.category_row.set_selected(sel_idx)
                except ValueError:
                    self.category_row.set_selected(0)
            else:
                self.category_row.set_selected(0)
            cat_group.add(self.category_row)
            vbox.append(cat_group)
        else:
            self.category_row = None

        # Fields header
        fields_header = Gtk.Label(label="Fields", xalign=0)
        fields_header.add_css_class("heading")
        fields_header.set_margin_start(4)
        vbox.append(fields_header)

        hint = Gtk.Label(label="Drag the handle to reorder", xalign=0)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        hint.set_margin_start(4)
        vbox.append(hint)

        # Reorderable field list
        self.fields_listbox = Gtk.ListBox()
        self.fields_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.fields_listbox.add_css_class("boxed-list")
        self.fields_listbox.add_css_class("field-editor-list")

        if entry:
            for field in entry["fields"]:
                row = FieldRow(self, field["label"], field["value"], field.get("sensitive", False))
                self.fields_listbox.append(row)
        else:
            self.fields_listbox.append(FieldRow(self, "Username", ""))
            self.fields_listbox.append(FieldRow(self, "Password", "", sensitive=True))

        vbox.append(self.fields_listbox)

        add_field_btn = Gtk.Button(label="Add Field", halign=Gtk.Align.START)
        add_field_btn.add_css_class("flat")
        add_field_btn.connect("clicked", self._on_add_field)
        vbox.append(add_field_btn)

        # Notes
        notes_group = Adw.PreferencesGroup(title="Notes")
        self.notes_view = Gtk.TextView()
        self.notes_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.notes_view.set_top_margin(8)
        self.notes_view.set_bottom_margin(8)
        self.notes_view.set_left_margin(8)
        self.notes_view.set_right_margin(8)
        if entry and entry.get("notes"):
            self.notes_view.get_buffer().set_text(entry["notes"])
        notes_frame = Gtk.Frame()
        notes_frame.set_child(self.notes_view)
        notes_frame.set_size_request(-1, 80)
        notes_group.add(notes_frame)
        vbox.append(notes_group)

        clamp.set_child(vbox)

        # Unsaved-changes guard (ROLO-0022): take over the close request so an accidental
        # Esc / close-button / Cancel with edits in flight prompts before discarding. A
        # successful Save bypasses this via force_close(). Snapshot taken last, once every
        # widget is populated, so it reflects the dialog's initial state.
        self.set_can_close(False)
        self.connect("close-attempt", self._on_close_attempt)
        self._initial_snapshot = self._snapshot()

    def _snapshot(self) -> tuple:
        """A comparable signature of the whole form — name, category, notes, and every field
        row. Two snapshots differ iff the user changed something (drives the dirty check)."""
        buf = self.notes_view.get_buffer()
        notes = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        category = self.category_row.get_selected() if self.category_row is not None else -1
        fields = tuple(
            (r.label_entry.get_text(), r.value_entry.get_text(), r.sens_check.get_active())
            for r in self._get_field_rows()
        )
        return (self.name_entry.get_text(), category, notes, fields)

    def _is_dirty(self) -> bool:
        return self._snapshot() != self._initial_snapshot

    def _on_close_attempt(self, _dialog):
        if not self._is_dirty():
            self.force_close()
            return
        self._confirm(
            "Discard changes?",
            "This entry has unsaved changes. Discard them?",
            "Discard", Adw.ResponseAppearance.DESTRUCTIVE, self.force_close,
        )

    def _confirm(self, heading, body, action_label, appearance, on_confirm):
        """Present a modal Cancel / <action> confirmation over this dialog, invoking
        on_confirm only when the user picks the action. Shared by the discard and
        duplicate-name prompts."""
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", action_label)
        dlg.set_response_appearance("ok", appearance)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda _d, r: on_confirm() if r == "ok" else None)
        dlg.present(self)

    def _on_add_field(self, btn):
        row = FieldRow(self, "", "")
        self.fields_listbox.append(row)
        row.label_entry.grab_focus()

    def _remove_field_row(self, row):
        self.fields_listbox.remove(row)

    def _reorder_field(self, dragged_row, target_row):
        """Move dragged_row to the position of target_row."""
        # Collect current order
        rows = self._get_field_rows()
        if dragged_row not in rows or target_row not in rows:
            return
        rows.remove(dragged_row)
        target_idx = rows.index(target_row)
        rows.insert(target_idx, dragged_row)

        # Rebuild listbox in new order
        for r in list(self._get_field_rows()):
            self.fields_listbox.remove(r)
        for r in rows:
            self.fields_listbox.append(r)

    def _get_field_rows(self) -> list:
        """Return all FieldRow children in current order."""
        rows = []
        idx = 0
        while True:
            row = self.fields_listbox.get_row_at_index(idx)
            if row is None:
                break
            rows.append(row)
            idx += 1
        return rows

    def _on_save(self, btn):
        name = self.name_entry.get_text().strip()
        if not name:
            return

        fields = []
        for row in self._get_field_rows():
            label = row.label_entry.get_text().strip()
            value = row.value_entry.get_text().strip()
            if label or value:
                fields.append({
                    "label": label or "Unlabeled",
                    "value": value,
                    "sensitive": row.sens_check.get_active(),
                })

        buf = self.notes_view.get_buffer()
        notes = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()

        # Extract category selection
        category = ""
        if self.category_row is not None:
            sel = self.category_row.get_selected()
            if sel > 0:  # 0 = "(None)"
                item = self.category_row.get_model().get_string(sel)
                if item:
                    category = item

        # Warn on a name that collides with another entry (ROLO-0023). exclude_id skips the
        # entry being edited so it isn't flagged as a duplicate of itself.
        dup = find_entry_by_name(self.main_win.vault, name, exclude_id=self.entry_id)
        if dup is not None:
            self._confirm(
                "Duplicate name",
                f'Another entry is already named "{name}". Save anyway?',
                "Save Anyway", Adw.ResponseAppearance.DEFAULT,
                lambda: self._commit(name, fields, notes, category),
            )
            return

        self._commit(name, fields, notes, category)

    def _commit(self, name, fields, notes, category):
        if self.entry_id:
            self.main_win._finish_edit(self.entry_id, name, fields, notes, category)
        else:
            self.main_win._finish_add(name, fields, notes, category)
        self.force_close()  # bypass the unsaved-changes guard — this is a deliberate save


# --------------------------------------------------------------------------
# Import preview dialog
# --------------------------------------------------------------------------


class ImportPreviewDialog(Adw.Dialog):
    def __init__(self, main_win, parsed, filepath):
        super().__init__()
        self.main_win = main_win
        self.parsed = parsed
        self.filepath = filepath
        self.checks = []

        header, clamp = make_dialog_scaffold(
            self, "Import Preview", width=500, height=480, clamp_max=460, margin=12, scrolled=True)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        import_btn = Gtk.Button(label="Import Selected")
        import_btn.add_css_class("suggested-action")
        import_btn.connect("clicked", self._on_import)
        header.pack_end(import_btn)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        info = Gtk.Label(label=f"Found {len(parsed)} entries in file.", xalign=0)
        info.add_css_class("heading")
        vbox.append(info)

        # Select all / none
        ctrl_box = Gtk.Box(spacing=8)
        sel_all = Gtk.Button(label="Select All")
        sel_all.add_css_class("flat")
        sel_all.connect("clicked", lambda b: self._set_all(True))
        sel_none = Gtk.Button(label="Select None")
        sel_none.add_css_class("flat")
        sel_none.connect("clicked", lambda b: self._set_all(False))
        ctrl_box.append(sel_all)
        ctrl_box.append(sel_none)
        vbox.append(ctrl_box)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        existing_names = {e["name"].lower() for e in main_win.vault["entries"].values()}

        for i, entry in enumerate(parsed):
            is_dup = entry["name"].lower() in existing_names
            row = Adw.ActionRow()
            row.set_title(GLib.markup_escape_text(entry["name"]))
            field_count = len(entry["fields"])
            notes_flag = " +notes" if entry.get("notes") else ""
            subtitle = f"{field_count} fields{notes_flag}"
            if is_dup:
                subtitle += "  (duplicate)"
            row.set_subtitle(subtitle)

            check = Gtk.CheckButton(active=not is_dup)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            self.checks.append((check, i))

            listbox.append(row)

        vbox.append(listbox)
        clamp.set_child(vbox)

    def _set_all(self, state):
        for check, _ in self.checks:
            check.set_active(state)

    def _on_import(self, btn):
        selected = [self.parsed[i] for check, i in self.checks if check.get_active()]
        if not selected:
            return
        self.main_win._finish_import(selected)
        self.close()


# --------------------------------------------------------------------------
# Change password dialog
# --------------------------------------------------------------------------


class ChangePasswordDialog(Adw.Dialog):
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win

        header, clamp = make_dialog_scaffold(
            self, "Change Master Password", width=380, height=-1, clamp_max=340, margin=24)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Change")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        pw_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        pw_list.add_css_class("boxed-list")

        self.current_pw = Adw.PasswordEntryRow(title="Current password")
        pw_list.append(self.current_pw)

        vbox.append(pw_list)
        vbox.append(Gtk.Separator())

        new_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        new_list.add_css_class("boxed-list")

        self.new_pw = Adw.PasswordEntryRow(title="New password")
        new_list.append(self.new_pw)

        self.confirm_pw = Adw.PasswordEntryRow(title="Confirm new password")
        new_list.append(self.confirm_pw)

        vbox.append(new_list)

        self.status = Gtk.Label()
        self.status.add_css_class("error")
        self.status.set_visible(False)
        vbox.append(self.status)

        clamp.set_child(vbox)

    def _on_save(self, btn):
        current = self.current_pw.get_text()
        if current != self.main_win.password:
            self.status.set_text("Incorrect current password.")
            self.status.set_visible(True)
            return

        new = self.new_pw.get_text()
        if len(new) < MIN_PASSWORD_LENGTH:
            self.status.set_text(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            self.status.set_visible(True)
            return

        confirm = self.confirm_pw.get_text()
        if new != confirm:
            self.status.set_text("Passwords do not match.")
            self.status.set_visible(True)
            return

        self.main_win._finish_change_password(new)
        self.close()


# --------------------------------------------------------------------------
# Restore password prompt dialog
# --------------------------------------------------------------------------


class RestorePasswordDialog(Adw.Dialog):
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win

        header, clamp = make_dialog_scaffold(
            self, "Restore from Backup", width=380, height=-1, clamp_max=340, margin=24)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        unlock_btn = Gtk.Button(label="Restore")
        unlock_btn.add_css_class("suggested-action")
        unlock_btn.connect("clicked", self._on_unlock)
        self._unlock_btn = unlock_btn
        header.pack_end(unlock_btn)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        info = Gtk.Label(
            label="Enter the master password for the backup vault.",
            wrap=True, xalign=0,
        )
        info.add_css_class("dim-label")
        vbox.append(info)

        pw_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        pw_list.add_css_class("boxed-list")
        self.pw_entry = Adw.PasswordEntryRow(title="Backup password")
        self.pw_entry.connect("activate", self._on_unlock)
        pw_list.append(self.pw_entry)
        vbox.append(pw_list)

        self.status = Gtk.Label()
        self.status.add_css_class("error")
        self.status.set_visible(False)
        vbox.append(self.status)

        clamp.set_child(vbox)

    def _on_unlock(self, *_args):
        pw = self.pw_entry.get_text()
        if not pw:
            self.status.set_text("Please enter the backup password.")
            self.status.set_visible(True)
            return

        self._unlock_btn.set_sensitive(False)
        self._unlock_btn.set_label("Decrypting...")

        import threading
        threading.Thread(
            target=self._try_unlock, args=(pw,), daemon=True
        ).start()

    def _try_unlock(self, pw):
        try:
            vault, salt = load_vault(pw, self.main_win._restore_path)
            GLib.idle_add(self._unlock_ok, vault, salt, pw)
        except InvalidToken:
            GLib.idle_add(self._unlock_fail, "Wrong password for this backup.")
        except Exception as e:
            GLib.idle_add(self._unlock_fail, str(e))

    def _unlock_ok(self, vault, salt, pw):
        self.main_win._finish_restore(vault, salt, pw)
        self.close()

    def _unlock_fail(self, msg):
        self._unlock_btn.set_sensitive(True)
        self._unlock_btn.set_label("Restore")
        self.status.set_text(msg)
        self.status.set_visible(True)


# --------------------------------------------------------------------------
# Category row for Manage Categories dialog
# --------------------------------------------------------------------------


class CategoryRow(Gtk.ListBoxRow):
    """A single category row with drag handle, name, count, rename, delete."""

    def __init__(self, dialog, name: str, count: int):
        super().__init__()
        self.dialog = dialog
        self.cat_name = name

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(4)
        box.set_margin_end(4)

        # Drag handle
        handle = Gtk.Image(icon_name="list-drag-handle-symbolic")
        handle.add_css_class("dim-label")
        handle.set_tooltip_text("Drag to reorder")
        box.append(handle)

        # Category name label
        self.name_label = Gtk.Label(label=name, xalign=0, hexpand=True)
        self.name_label.set_ellipsize(3)
        box.append(self.name_label)

        # Count badge
        count_lbl = Gtk.Label(label=str(count))
        count_lbl.add_css_class("category-count")
        box.append(count_lbl)

        # Rename button
        rename_btn = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Rename")
        rename_btn.add_css_class("flat")
        rename_btn.connect("clicked", lambda b: self.dialog._rename_category(self))
        box.append(rename_btn)

        # Delete button
        del_btn = Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text="Delete")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("error")
        del_btn.connect("clicked", lambda b: self.dialog._delete_category(self))
        box.append(del_btn)

        self.set_child(box)

        # Drag source on handle
        drag_src = Gtk.DragSource()
        drag_src.set_actions(Gdk.DragAction.MOVE)
        drag_src.connect("prepare", self._on_drag_prepare)
        drag_src.connect("drag-begin", self._on_drag_begin)
        handle.add_controller(drag_src)

        # Drop target on whole row
        drop = Gtk.DropTarget(actions=Gdk.DragAction.MOVE)
        drop.set_gtypes([CategoryRow])
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

    def _on_drag_prepare(self, source, x, y):
        return Gdk.ContentProvider.new_for_value(self)

    def _on_drag_begin(self, source, drag):
        icon = Gtk.DragIcon.get_for_drag(drag)
        lbl = Gtk.Label(label=self.cat_name)
        lbl.add_css_class("caption")
        lbl.set_margin_top(6)
        lbl.set_margin_bottom(6)
        lbl.set_margin_start(12)
        lbl.set_margin_end(12)
        icon.set_child(lbl)

    def _on_drop(self, target, dragged_row, x, y):
        if dragged_row is self:
            return False
        self.dialog._reorder_category(dragged_row, self)
        return True


# --------------------------------------------------------------------------
# Manage Categories dialog
# --------------------------------------------------------------------------


class ManageCategoriesDialog(Adw.Dialog):
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win

        header, clamp = make_dialog_scaffold(
            self, "Manage Categories", width=420, height=460, clamp_max=400, margin=12, scrolled=True)

        done_btn = Gtk.Button(label="Done")
        done_btn.add_css_class("suggested-action")
        done_btn.connect("clicked", lambda b: self.close())
        header.pack_end(done_btn)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Add new category row
        add_box = Gtk.Box(spacing=8)
        self.new_cat_entry = Gtk.Entry(placeholder_text="New category name...", hexpand=True)
        self.new_cat_entry.connect("activate", lambda e: self._add_category())
        add_box.append(self.new_cat_entry)
        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda b: self._add_category())
        add_box.append(add_btn)
        vbox.append(add_box)

        # Category list
        self.cat_listbox = Gtk.ListBox()
        self.cat_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.cat_listbox.add_css_class("boxed-list")
        vbox.append(self.cat_listbox)

        self._rebuild_list()

        clamp.set_child(vbox)

    def _rebuild_list(self):
        clear_container(self.cat_listbox)

        groups = entries_by_category(self.main_win.vault)
        for cat_name in self.main_win.vault["categories"]:
            count = len(groups.get(cat_name, []))
            row = CategoryRow(self, cat_name, count)
            self.cat_listbox.append(row)

    def _add_category(self):
        name = self.new_cat_entry.get_text().strip()
        if not name:
            return
        if add_category(self.main_win.vault, name):
            self.main_win._save()
            self.new_cat_entry.set_text("")
            self._rebuild_list()
            self.main_win._refresh_list()

    def _rename_category(self, row):
        dialog = Adw.AlertDialog(heading="Rename category", body=f'Enter a new name for "{row.cat_name}":')
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry(text=row.cat_name)
        entry.set_margin_start(24)
        entry.set_margin_end(24)
        dialog.set_extra_child(entry)

        def on_response(d, response):
            if response == "rename":
                new_name = entry.get_text().strip()
                if new_name and new_name != row.cat_name and new_name not in self.main_win.vault["categories"]:
                    old_name = row.cat_name
                    rename_category(self.main_win.vault, old_name, new_name)
                    # Update collapsed set
                    if old_name in self.main_win._collapsed_categories:
                        self.main_win._collapsed_categories.discard(old_name)
                        self.main_win._collapsed_categories.add(new_name)
                    self.main_win._save()
                    self._rebuild_list()
                    self.main_win._refresh_list()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _delete_category(self, row):
        groups = entries_by_category(self.main_win.vault)
        count = len(groups.get(row.cat_name, []))
        body = f'Delete category "{row.cat_name}"?'
        if count:
            body += f"\n{count} entries will be moved to Uncategorised."

        dialog = Adw.AlertDialog(heading="Delete category", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(d, response):
            if response == "delete":
                self.main_win._collapsed_categories.discard(row.cat_name)
                delete_category(self.main_win.vault, row.cat_name)
                self.main_win._save()
                self._rebuild_list()
                self.main_win._refresh_list()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _reorder_category(self, dragged_row, target_row):
        cats = self.main_win.vault["categories"]
        old_idx = cats.index(dragged_row.cat_name)
        cats.pop(old_idx)
        new_idx = cats.index(target_row.cat_name)
        cats.insert(new_idx, dragged_row.cat_name)
        self.main_win._save()
        self._rebuild_list()
        self.main_win._refresh_list()

    def _get_cat_rows(self):
        rows = []
        idx = 0
        while True:
            row = self.cat_listbox.get_row_at_index(idx)
            if row is None:
                break
            rows.append(row)
            idx += 1
        return rows


# ===========================================================================
# Application
# ===========================================================================


CUSTOM_CSS = """
/* ── Accent overrides ── */
@define-color accent_bg_color #3584e4;
@define-color accent_color #78aeed;

/* ══════════════════════════════════════════════
   Gradient backgrounds
   ══════════════════════════════════════════════ */

/* Main window background: deep dark gradient */
.main-paned {
    background-image: linear-gradient(160deg, #0d1117 0%, #161b22 35%, #0f1923 65%, #0d1117 100%);
}

/* Sidebar: subtle darker panel */
.sidebar-box {
    background-image: linear-gradient(180deg, rgba(13,17,23,0.95) 0%, rgba(22,27,34,0.9) 100%);
    border-right: 1px solid rgba(120,174,237,0.08);
}

/* Unlock dialog window */
window.background {
    background-image: linear-gradient(160deg, #0d1117 0%, #131a24 50%, #0d1117 100%);
}

/* ══════════════════════════════════════════════
   Glass effect for cards & panels
   ══════════════════════════════════════════════ */

/* Boxed lists (field cards, import list, password rows) */
.boxed-list {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    box-shadow:
        0 4px 16px rgba(0,0,0,0.3),
        inset 0 1px 0 rgba(255,255,255,0.05);
}

.boxed-list row {
    background: transparent;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}

.boxed-list row:last-child {
    border-bottom: none;
}

/* ── Field category left-border colors ── */

/*  Credential (password, pin, authenticator) — amber */
.field-credential {
    border-left: 3px solid rgba(229,165,10,0.7);
}

/*  Key / Token / Secret — purple */
.field-key {
    border-left: 3px solid rgba(145,65,172,0.7);
}

/*  Identity (username, email, account) — blue */
.field-identity {
    border-left: 3px solid rgba(53,132,228,0.7);
}

/*  URL / Link — green */
.field-url {
    border-left: 3px solid rgba(38,162,105,0.7);
}

/*  Date / Expiry / Subscription — orange */
.field-date {
    border-left: 3px solid rgba(230,97,0,0.7);
}

/*  Other / uncategorised — subtle grey */
.field-other {
    border-left: 3px solid rgba(94,92,100,0.5);
}

/* Notes frame: glass card — cyan, distinct from URL green & identity blue */
.notes-frame {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(42,161,179,0.15);
    border-left: 3px solid rgba(42,161,179,0.6);
    border-radius: 10px;
    padding: 4px 8px;
    box-shadow:
        0 2px 12px rgba(0,0,0,0.25),
        inset 0 1px 0 rgba(255,255,255,0.04);
}

/* Navigation sidebar rows: glass on hover/select */
.navigation-sidebar {
    background: transparent;
}

.navigation-sidebar row {
    border-radius: 8px;
    margin: 2px 6px;
    padding: 2px;
    transition: background 150ms ease;
}

.navigation-sidebar row:hover {
    background: rgba(255,255,255,0.04);
}

.navigation-sidebar row:selected {
    background: rgba(53,132,228,0.15);
    border-left: 3px solid #3584e4;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
}

/* Action buttons: glass pill style */
.reveal-btn, .edit-btn {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    padding: 6px 14px;
    transition: background 150ms ease, border-color 150ms ease;
}

.reveal-btn:hover {
    background: rgba(245,194,17,0.1);
    border-color: rgba(245,194,17,0.25);
}

.edit-btn:hover {
    background: rgba(120,174,237,0.1);
    border-color: rgba(120,174,237,0.25);
}

/* Search entry: glass style */
.sidebar-box searchentry {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
}

.sidebar-box searchentry:focus-within {
    background: rgba(255,255,255,0.06);
    border-color: rgba(53,132,228,0.4);
    box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.03),
        0 0 0 2px rgba(53,132,228,0.15);
}

/* ══════════════════════════════════════════════
   Text & color accents
   ══════════════════════════════════════════════ */

/* Entry name in detail view */
.entry-title {
    color: #78aeed;
    text-shadow: 0 0 20px rgba(53,132,228,0.3);
}

/* Sensitive field mask */
.field-masked {
    color: #555d6b;
    font-style: italic;
    letter-spacing: 2px;
}

/* Revealed sensitive value - amber glow */
.field-revealed-sensitive {
    color: #f5c211;
    text-shadow: 0 0 12px rgba(245,194,17,0.2);
}

/* TOTP live code row (ROLO-0006) */
.totp-row {
    opacity: 0.92;
}
.totp-code {
    font-family: monospace;
    font-size: 1.25em;
    font-weight: bold;
    letter-spacing: 2px;
    color: #5ca8ff;
}
.totp-remaining {
    font-size: 0.85em;
    color: #8b93a1;
    min-width: 26px;
}

/* Copy button */
.copy-btn {
    border-radius: 6px;
    transition: color 150ms ease, background 150ms ease;
}

.copy-btn:hover {
    color: #78aeed;
    background: rgba(120,174,237,0.1);
}

/* Timestamp styling */
.timestamp {
    color: #484f58;
    font-size: 0.85em;
}

/* Reveal button */
.reveal-btn {
    color: #f5c211;
}

/* Edit button */
.edit-btn {
    color: #78aeed;
}

/* Count label */
.count-label {
    color: #78aeed;
    font-weight: bold;
    text-shadow: 0 0 16px rgba(53,132,228,0.2);
}

/* Unlock dialog title */
.unlock-title {
    color: #78aeed;
    font-size: 1.6em;
    font-weight: 800;
    text-shadow: 0 0 24px rgba(53,132,228,0.35);
}

/* Separator gets a subtle glow */
separator {
    background: linear-gradient(90deg,
        transparent 0%,
        rgba(53,132,228,0.25) 50%,
        transparent 100%);
    min-height: 1px;
}

/* Header bar: blend with gradient */
headerbar {
    background: rgba(13,17,23,0.85);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    box-shadow: 0 1px 4px rgba(0,0,0,0.3);
}

/* Suggested-action buttons (Create Vault, Unlock, Save, Import) */
button.suggested-action {
    background: linear-gradient(135deg, #2563b0 0%, #3584e4 100%);
    border: 1px solid rgba(120,174,237,0.3);
    box-shadow:
        0 2px 8px rgba(53,132,228,0.3),
        inset 0 1px 0 rgba(255,255,255,0.1);
}

button.suggested-action:hover {
    background: linear-gradient(135deg, #2d6fbf 0%, #4a94e8 100%);
    box-shadow:
        0 4px 16px rgba(53,132,228,0.4),
        inset 0 1px 0 rgba(255,255,255,0.12);
}

/* Destructive button glow */
button.destructive-action {
    box-shadow: 0 2px 8px rgba(224,27,36,0.25);
}

button.destructive-action:hover {
    box-shadow: 0 4px 16px rgba(224,27,36,0.35);
}

/* Password entry rows: blend with glass */
row.entry {
    background: transparent;
}

/* ── Field editor (Add/Edit dialog) ── */
.field-editor-list {
    background: rgba(255,255,255,0.03);
}

.field-editor-list row {
    background: transparent;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    transition: background 150ms ease;
}

.field-editor-list row:hover {
    background: rgba(255,255,255,0.02);
}

/* ── Category header rows in sidebar ── */
.category-header-row {
    background: transparent;
}

.category-header-row:hover {
    background: rgba(255,255,255,0.02);
}

.navigation-sidebar .category-header-row:selected {
    background: transparent;
    border-left: none;
    box-shadow: none;
}

.category-header-label {
    color: #6e7681;
    font-size: 0.75em;
    font-weight: 800;
    letter-spacing: 1.5px;
}

.category-count {
    background: rgba(255,255,255,0.06);
    border-radius: 10px;
    color: #6e7681;
    font-size: 0.75em;
    font-weight: 600;
    min-width: 20px;
    padding: 1px 6px;
}

.category-drop-hover {
    background: rgba(53,132,228,0.15);
    border-radius: 8px;
    box-shadow: 0 0 8px rgba(53,132,228,0.3);
}
"""


class PasswordHealthDialog(Adw.Dialog):
    """Read-only checkup listing weak or reused secrets, worst first (ROLO-0008).

    All scoring happens in audit_passwords() over the in-memory vault; nothing leaves the process.
    """

    def __init__(self, main_win):
        super().__init__()
        _, clamp = make_dialog_scaffold(
            self, "Password Health", width=460, height=520, clamp_max=440, margin=16, scrolled=True)

        findings = audit_passwords(main_win.vault)
        weak = [f for f in findings if f["strength"] <= 2]
        reused = [f for f in findings if f["reused"]]

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        if not findings:
            summary = "No passwords stored yet."
        elif not weak and not reused:
            summary = f"All {len(findings)} passwords look strong."
        else:
            parts = []
            if weak:
                parts.append(f"{len(weak)} weak or fair")
            if reused:
                parts.append(f"{len(reused)} reused")
            summary = "   ·   ".join(parts)
        summary_lbl = Gtk.Label(label=summary, xalign=0, wrap=True)
        summary_lbl.add_css_class("title-4")
        vbox.append(summary_lbl)

        if findings:
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            for f in findings:
                row = Adw.ActionRow(title=f["entry_name"], subtitle=f["label"])
                chips = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
                strength_chip = Gtk.Label(label=f["strength_label"])
                strength_chip.add_css_class("caption")
                strength_chip.add_css_class(
                    "error" if f["strength"] <= 1 else
                    "warning" if f["strength"] == 2 else "success")
                chips.append(strength_chip)
                if f["reused"]:
                    reuse_chip = Gtk.Label(label="Reused")
                    reuse_chip.add_css_class("caption")
                    reuse_chip.add_css_class("warning")
                    chips.append(reuse_chip)
                row.add_suffix(chips)
                listbox.append(row)
            vbox.append(listbox)

        clamp.set_child(vbox)


class ShortcutsDialog(Adw.Dialog):
    """Keyboard-shortcut reference (Ctrl+?). Hand-built because Gtk.ShortcutsWindow is
    deprecated as of GTK 4.18 (this ships against 4.22)."""

    SHORTCUTS = [
        ("<Control>f", "Focus search"),
        ("<Control>n", "Add entry"),
        ("<Control><Shift>c", "Copy password / secret"),
        ("<Control>l", "Lock vault"),
        ("Escape", "Clear search"),
        ("<Control>question", "Keyboard shortcuts"),
    ]

    def __init__(self):
        super().__init__()
        _, clamp = make_dialog_scaffold(
            self, "Keyboard Shortcuts", width=380, height=-1, clamp_max=340, margin=24)

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        for accel, desc in self.SHORTCUTS:
            row = Adw.ActionRow(title=desc)
            row.add_suffix(Gtk.ShortcutLabel(accelerator=accel, valign=Gtk.Align.CENTER))
            listbox.append(row)

        clamp.set_child(listbox)


class RolodexApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.vault_path = VAULT_FILE

    def do_startup(self):
        Adw.Application.do_startup(self)
        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(CUSTOM_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def do_activate(self):
        is_new = not os.path.exists(self.vault_path)
        win = UnlockDialog(self, self.vault_path, is_new)
        win.present()

    def open_main(self, vault, salt, password, vault_path):
        win = MainWindow(self, vault, salt, password, vault_path)
        win.present()


def main():
    if "--selftest" in sys.argv[1:]:
        # Packaging smoke test. Reaching this line means every module-level import — including
        # `from gi.repository import Adw, Gdk, Gio, GLib, Gtk` (which loads the GTK/libadwaita
        # typelibs + shared libraries) and `cryptography` — succeeded, so the bundled runtime is
        # intact on this OS. CI runs the built binary with --selftest to fail any build whose
        # GTK stack didn't bundle correctly. Exits without starting the GUI (no display needed).
        print("rolodex selftest: OK (GTK/Adw/cryptography loaded)")
        return
    app = RolodexApp()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
