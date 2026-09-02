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
import tempfile
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

# The running app's own version (ROLO-0037). Before this existed the version lived only in the
# CHANGELOG heading and the git tag, and an updater cannot compare against a version the process
# cannot read. .claude/bump.json rewrites this line and its post_check asserts it matches the
# topmost dated CHANGELOG heading, so the two cannot drift.
__version__ = "1.3.1"

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
MIN_PASSWORD_LENGTH = 12
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
    """Atomically write bytes to path with owner-only (0600) permissions.

    Every secret-writing path (the vault, the plaintext export) goes through here so the
    0600 mode and the write live in exactly one place (ROLO-0019). The write is atomic:
    data lands in a temp file in the same directory, is fsync'd, then os.replace()'d into
    place — so an interrupted write (crash, disk-full, power loss) can never truncate or
    partially overwrite an existing file. That matters most for the vault, which is the
    user's only copy of their credentials. mkstemp creates the temp 0600, and os.replace
    carries that mode onto the destination.
    """
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".rolodex-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
    except BaseException:
        # BaseException, not Exception: KeyboardInterrupt and SystemExit are not Exceptions,
        # and a Ctrl-C after the fsync would otherwise strand a temp holding the complete
        # ciphertext next to the vault (ROLO-0060). Nothing can cover a power cut or SIGKILL
        # -- no handler runs -- which is why INV-16 states that limit rather than hiding it.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_vault_with_key(vault_data: dict, key: bytes, salt: bytes, path: str) -> None:
    """Encrypt and write the whole vault under an ALREADY-DERIVED key.

    derive_key is deliberately expensive, so a caller that already holds the key for this
    salt -- an open window, which derived it at unlock -- must not pay for it again on every
    save (ROLO-0043). `key` and `salt` must be the pair that belong together: the salt is
    stored in the clear beside the ciphertext, so writing a key derived from a different salt
    produces a vault no password can open.
    """
    f = Fernet(key)
    plaintext = json.dumps(vault_data, ensure_ascii=False).encode("utf-8")
    ciphertext = f.encrypt(plaintext)
    write_private_file(path, MAGIC + salt + ciphertext)


def save_vault(vault_data: dict, password: str, salt: bytes, path: str) -> None:
    save_vault_with_key(vault_data, derive_key(password, salt), salt, path)


def load_vault_with_key(password: str, path: str) -> tuple[dict, bytes, bytes]:
    """Decrypt the vault, returning the derived key alongside it.

    Unlocking already runs the KDF once. Handing the key back lets the session keep it
    instead of deriving it a second time for the first save (ROLO-0043).
    """
    with open(path, "rb") as fp:
        magic = fp.read(4)
        if magic != MAGIC:
            raise ValueError("Not a valid vault file")
        salt = fp.read(16)
        # INV-6 is "the exact 16 bytes", and a short read here is silently accepted by
        # derive_key. A truncated vault -- a partial copy, an interrupted sync -- would then
        # fail decryption with InvalidToken, which the unlock dialog renders as "Wrong
        # password." Reporting a corrupt file as a forgotten password is the worst available
        # error for an app with no recovery path: the plausible response is to delete and start
        # again, destroying a file a backup restore could still have salvaged.
        if len(salt) != 16:
            raise ValueError("Vault file is truncated or corrupt")
        ciphertext = fp.read()
    key = derive_key(password, salt)
    f = Fernet(key)
    plaintext = f.decrypt(ciphertext)
    return json.loads(plaintext.decode("utf-8")), salt, key


def load_vault(password: str, path: str) -> tuple[dict, bytes]:
    vault, salt, _key = load_vault_with_key(password, path)
    return vault, salt


def create_vault_with_key(password: str, path: str) -> tuple[dict, bytes, bytes]:
    """Create an empty vault, returning its derived key alongside (see load_vault_with_key)."""
    salt = os.urandom(16)
    key = derive_key(password, salt)
    vault_data = {"version": 2, "categories": [], "entries": {}}
    save_vault_with_key(vault_data, key, salt, path)
    return vault_data, salt, key


def create_vault(password: str, path: str) -> tuple[dict, bytes]:
    vault_data, salt, _key = create_vault_with_key(password, path)
    return vault_data, salt


def migrate_vault(vault: dict) -> dict:
    """Upgrade vault data to the latest version (v2). Idempotent.

    Refuses a vault newer than this build understands rather than relabelling it: the version
    stamp was unconditional, so a future v3 vault opened here was rewritten as v2 and the lie
    persisted on the next save. Migration is one-way by design (DESIGN.md), so there is no
    recovering from that.
    """
    if not isinstance(vault, dict) or not isinstance(vault.get("entries"), dict):
        raise ValueError("Vault contents are not a valid vault")
    version = vault.get("version", 1)
    if isinstance(version, int) and version > 2:
        raise ValueError(
            f"This vault was written by a newer version of Rolodex (format v{version}). "
            "Upgrade Rolodex to open it."
        )
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
    # Strip any whitespace, not just U+0020: the docstring promises tolerance, and a seed pasted
    # with a tab, newline or non-breaking space would otherwise be rejected as "not a seed".
    stripped = re.sub(r"[\s\-]+", "", s)
    if not stripped:
        return None
    # Validate BEFORE folding case, and against the ASCII ranges only. str.upper() applies full
    # Unicode case mapping, so characters outside base32 fold INTO it ('ı' -> 'I',
    # 'ſ' -> 'S') and would decode silently to a WRONG secret rather than failing. Checking
    # after .upper() cannot catch that -- by then the fold has already happened and the
    # character is a legitimate base32 letter.
    if not re.fullmatch(r"[A-Za-z2-7]*", stripped):
        return None
    cleaned = stripped.upper()
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
    try:
        parsed = urllib.parse.urlparse(uri)
    except ValueError:
        # urlsplit raises on an unbalanced or invalid bracketed host ("otpauth://[totp"). This
        # runs per field from the detail-view render, so an exception here strands the whole
        # entry behind an undrawable pane -- and parse_totp_field's docstring promises it never
        # raises on user data.
        return None
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
    # RFC 4226 defines Digit as 6-8 and its DIGITS_POWER table stops at 10^8; the Key URI Format
    # names 6 and 8. Dynamic truncation yields at most 2147483647, so a 9- or 10-digit code is
    # degenerate -- its leading digits can never span their full range. An unbounded period
    # likewise produces a countdown the ring cannot render.
    if digits not in (6, 7, 8) or not (1 <= period <= 300):
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
    # Require ≥80 bits. NOT an RFC floor -- RFC 4226 §4 R6 requires at least 128 bits and
    # recommends 160, but Google Authenticator's standard 16-character seed decodes to 80, so
    # raising this to match the RFC would reject the most common seed in existence. It is a
    # heuristic threshold chosen to keep short base32-valid prose
    # like "just some words" from being mistaken for a seed when guessing off a bare value.
    if not secret or len(secret) < 10:
        return None
    return {"secret": secret, "digits": 6, "period": 30, "algorithm": "sha1"}


# Password health (ROLO-0008) — all analysis runs in-process over the decrypted vault.
def field_is_sensitive(field: dict) -> bool:
    """Whether a stored field must be masked in the UI.

    The stored `sensitive` flag OR a recognised TOTP seed. The two keyword sets do not agree --
    SENSITIVE_KEYWORDS and TOTP_LABEL_KEYWORDS overlap only on "authenticator" -- so a field
    labelled "2FA", "TOTP", "OTP" or "One-time" was stored non-sensitive and then rendered in
    permanent cleartext beside the live code derived from it. Routing through parse_totp_field
    also covers an otpauth:// URI pasted under ANY label, whose secret= parameter no keyword
    list could have caught. Checked at render as well as at save, so entries already sitting in
    a vault are masked too rather than only newly edited ones.
    """
    if field.get("sensitive"):
        return True
    return parse_totp_field(field.get("label", ""), field.get("value", "")) is not None


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
    reused, reuse_count}. `reuse_count` is the number of distinct ENTRIES sharing this value and
    `reused` is True when that exceeds one. Pure — nothing leaves the process.
    """
    # Reuse is counted across entries, not fields, because the risk it names is one secret
    # protecting two different accounts. Two sensitive fields inside a single entry -- a password
    # and its backup password, say -- are one account, and flagging those is noise that teaches
    # the user to ignore the warning (ROLO-0066).
    entry_ids: dict[str, set[str]] = {}
    for eid, entry in vault["entries"].items():
        for f in entry["fields"]:
            if f.get("sensitive") and f.get("value"):
                entry_ids.setdefault(f["value"], set()).add(eid)

    findings = []
    for eid, entry in vault["entries"].items():
        for f in entry["fields"]:
            value = f.get("value", "")
            if not f.get("sensitive") or not value:
                continue
            score = password_strength(value)
            reuse_count = len(entry_ids.get(value, ()))
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
    # Uniqueness is categories.md INV-1. The sole caller guards this today, so the check is
    # defence in depth -- but renaming onto an existing name would leave two identical entries
    # in the ordered list, which the sidebar renders twice and delete_category half-removes.
    if new_name != old_name and new_name in vault["categories"]:
        raise ValueError(f"A category named {new_name!r} already exists")
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
        # str.split never returns [], so the old `if not lines` guard was dead code: an empty or
        # whitespace-only file produced one block of [""], hence one entry with an empty name.
        # That defeated the caller's `if not parsed` check, so "No entries found in file."
        # (INV-5) was unreachable and importing wrote a nameless entry the editor forbids.
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        name = lines[0].rstrip(":").strip()
        fields = []
        notes_lines = []
        for line in lines[1:]:
            match = re.match(r"^([^:]+?):\s+(.+)$", line)
            if match:
                label = match.group(1).strip()
                value = match.group(2).strip()
                fields.append({
                    "label": label,
                    "value": value,
                    "sensitive": is_sensitive_label(label) or parse_totp_field(label, value) is not None,
                })
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
    # One char from each class first (up to length), then fill from the combined pool. The
    # per-class draws are shuffled BEFORE the [:length] slice: truncating them in the fixed
    # lower/upper/digits/symbols order meant generate_password(length=2) could only ever return
    # a lowercase and an uppercase character, never a digit or symbol.
    seeded = [secrets.choice(pool) for pool in pools]
    secrets.SystemRandom().shuffle(seeded)
    chars = seeded[:length]
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
        ["pbpaste"],  # macOS
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
        ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],  # Windows
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
        ["pbcopy"],  # macOS
        ["wl-copy", "--trim-newline"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["clip.exe"],  # Windows
    ]:
        if shutil.which(cmd[0]):
            try:
                proc = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=5)
                # Fall through to the next tool on a non-zero exit, exactly as read_clipboard
                # does. Returning here unconditionally meant that wl-clipboard merely being
                # INSTALLED under an X11 session -- which several distros arrange by default --
                # made every copy fail, because wl-copy exits non-zero with no Wayland display,
                # while a working xclip sat untried on the next line.
                if proc.returncode == 0:
                    return True
            except (subprocess.TimeoutExpired, OSError):
                continue
    return False


# ===========================================================================
# ---------------------------------------------------------------------------
# Window geometry config
# ---------------------------------------------------------------------------


def load_config(path: str | None = None) -> dict:
    """Read .rolodex.conf, returning {} for anything that is not a JSON object.

    The isinstance check is load-bearing, not defensive padding: README documents this file as
    hand-editable, and valid non-object JSON (`null`, `[]`, `5`) satisfied json.load and then
    raised AttributeError out of MainWindow.__init__ -- so the app failed to open its window at
    all, with an unhandled traceback rather than a message.
    """
    try:
        with open(path or CONFIG_FILE, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def config_int(conf: dict, key: str, default: int) -> int:
    """An int from a hand-editable config file, falling back rather than raising.

    `int("five")` inside MainWindow.__init__ raised from a GLib.idle_add callback after the
    unlock dialog had already disabled its button, leaving it stuck on "Unlocking..." forever
    with the vault decrypted in memory and nothing on screen to say why.
    """
    try:
        return int(conf.get(key, default))
    except (TypeError, ValueError):
        return default


def save_config(data: dict, path: str | None = None) -> None:
    try:
        existing = load_config(path)
        existing.update(data)
        # Atomic: open(..., "w") truncates first, so a kill or ENOSPC between truncate and flush
        # left a partial file that the next load_config read as {} -- silently resetting the
        # window geometry, BOTH security timeouts, skipped_update_version and the
        # check_for_updates opt-in, so the app quietly stopped checking for the security
        # updates ROLO-0037 exists to deliver.
        target = path or CONFIG_FILE
        tmp = f"{target}.tmp"
        with open(tmp, "w") as f:
            json.dump(existing, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except OSError:
        # Best-effort: .rolodex.conf holds only non-secret prefs (window geometry,
        # timeouts). If it can't be written we drop the update rather than interrupt
        # the user — there is nothing here worth surfacing an error or losing work over.
        pass


# ===========================================================================
# ---------------------------------------------------------------------------
# Opt-in signed auto-update (ROLO-0037)
# Contract: docs/specs/ROLO-0037-auto-update.md
#
# OFF by default (INV-1) and the app's only network egress (INV-3). Nothing here reads the
# vault or the master password (INV-4). urllib.request is imported INSIDE the fetch helpers
# and never at module scope, so `import rolodex` does not load it (INV-12) -- note the
# module-scope `urllib.parse` near the top is for TOTP otpauth:// parsing and is expected.
# ---------------------------------------------------------------------------

# The release-signing public key, base64 of 32 raw Ed25519 bytes (D3/D4).
#
# THIS IS A PLACEHOLDER: 32 zero bytes. It loads cleanly and rejects every signature, so until
# a real key is generated the feature FAILS CLOSED -- it can offer an update and can never
# install one (INV-11). Generate the real pair with scripts/gen-signing-key.py, paste the
# public half here, and keep the private half as a GitHub Actions secret and nowhere else.
RELEASE_PUBLIC_KEY_B64 = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

GITHUB_OWNER = "milnet01"
GITHUB_REPO = "rolodex"

# The asset each build downloads, keyed by (sys.platform, platform.machine()) -- D2. The names
# are build.yml's matrix, not invented here. Matching is EQUALITY, never prefix or substring:
# under those the release's own required <asset>.sig is a second match, so the ambiguity guard
# would fire on every well-formed release and no update would ever be offered (INV-5).
#
# win32 is deliberately ABSENT. Windows is deferred (S4) and this mechanism does not work
# there -- os.replace refuses a locked .exe and the relaunch needs /bin/sh -- so it is refused
# up front by is_update_supported() rather than after a download (INV-2).
PLATFORM_ASSETS = {
    ("linux", "x86_64"): "rolodex-linux-x86_64",
    ("darwin", "arm64"): "rolodex-macos-arm64",
}

# Resource bounds (INV-9). The asset cap has headroom over what build.yml currently produces;
# re-derive with `gh release view <tag> --json assets -q '.assets[].size'` before lowering it,
# because a cap under the real artifact aborts every genuine update while a synthetic
# over-cap test still passes.
MAX_UPDATE_BYTES = 250 * 1024 * 1024
MAX_API_BYTES = 1024 * 1024
MAX_SIG_BYTES = 4096
UPDATE_TIMEOUT_S = 30
_DOWNLOAD_CHUNK = 64 * 1024

UPDATE_ENABLED_KEY = "check_for_updates"
UPDATE_SKIPPED_KEY = "skipped_update_version"


class UpdateError(Exception):
    """An update could not be fetched, staged or installed."""


class UpdateVerificationError(UpdateError):
    """A download's signature did not verify against the built-in public key (INV-8).

    Deliberately a subclass of UpdateError so a caller may catch the signature case on its own
    or catch everything with one clause.
    """


def parse_version(text: str):
    """Parse ``N(.N)*`` (optional leading v/V) to an int tuple, or None if unusable (D10).

    ``segment.isdigit()`` -- not ``int()`` -- is the guard. int() quietly accepts "1_0", " 1",
    "+1" and Unicode digits, every one of which must make the parse fail so the caller treats
    the version as unusable rather than comparing a number the tag never carried.
    """
    if not isinstance(text, str):
        return None
    if text[:1] in ("v", "V"):
        text = text[1:]
    if not text:
        return None
    out = []
    for segment in text.split("."):
        if not (segment.isascii() and segment.isdigit()):
            return None
        out.append(int(segment))
    return tuple(out)


def version_gt(latest, current) -> bool:
    """True iff *latest* is strictly greater, zero-padding the shorter tuple so that
    (0, 1) and (0, 1, 0) compare EQUAL rather than one being newer (D10)."""
    width = max(len(latest), len(current))
    return latest + (0,) * (width - len(latest)) > current + (0,) * (width - len(current))


def version_string(tag: str) -> str:
    """A tag's bare version -- one leading v/V stripped. This is the form stored as the
    skipped version and shown to the user."""
    return tag[1:] if tag[:1] in ("v", "V") else tag


def platform_asset_name():
    """This build's release asset name, or None where self-update is unsupported (D2)."""
    import platform

    return PLATFORM_ASSETS.get((sys.platform, platform.machine()))


def detect_installer():
    """The path of the binary to replace, or None where self-update cannot run (INV-2).

    None off a frozen build (a source checkout or a distro package -- updating those is the
    packager's job), and None on any platform without an asset, which includes Windows.
    """
    if not getattr(sys, "frozen", False):
        return None
    if platform_asset_name() is None:
        return None
    return sys.executable


def is_update_supported() -> bool:
    """Whether self-update can run on this build (INV-2). The preference is shown but
    disabled, with a tooltip, when this is False."""
    return detect_installer() is not None


def update_check_enabled(path: str | None = None) -> bool:
    """Whether the user opted in. OFF unless the stored value is exactly boolean True --
    absent (a fresh install), false, or any malformed value all read as off (INV-1)."""
    return load_config(path).get(UPDATE_ENABLED_KEY) is True


def set_update_check_enabled(enabled: bool, path: str | None = None) -> None:
    save_config({UPDATE_ENABLED_KEY: bool(enabled)}, path)


def update_skipped_version(path: str | None = None) -> str:
    value = load_config(path).get(UPDATE_SKIPPED_KEY)
    return value if isinstance(value, str) else ""


def skip_update_version(version: str, path: str | None = None) -> None:
    """Persist a skipped version (INV-7). save_config swallows OSError by design, so a skip
    that cannot be written is dropped silently and the version is offered again next launch.
    That is accepted rather than made fatal -- see the spec's INV-7."""
    save_config({UPDATE_SKIPPED_KEY: version}, path)


def select_update_assets(assets, asset_name: str):
    """From a release's assets[] return (asset_url, sig_url), or None (INV-5).

    EQUALITY, not endswith/startswith -- see PLATFORM_ASSETS. Requires exactly one asset named
    *asset_name* and exactly one named *asset_name* + ".sig"; a duplicate of either fails safe,
    because ambiguity about which bytes to install is not something to guess at.
    """
    if not isinstance(assets, list):
        return None
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == asset_name]
    sigs = [a for a in assets if isinstance(a, dict) and a.get("name") == asset_name + ".sig"]
    if len(matches) != 1 or len(sigs) != 1:
        return None
    asset_url = matches[0].get("browser_download_url")
    sig_url = sigs[0].get("browser_download_url")
    if not asset_url or not sig_url:
        return None
    return asset_url, sig_url


def release_public_key():
    """The built-in release-signing public key (INV-8/INV-11)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    return Ed25519PublicKey.from_public_bytes(base64.b64decode(RELEASE_PUBLIC_KEY_B64))


def _require_https(url: str) -> None:
    """Refuse any non-https URL (INV-9). Defence in depth: even a tampered API response
    pointing an asset at http:// or file:// is never opened."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise UpdateError("refusing a non-https update URL")


def _opener():
    """A urllib opener that verifies TLS and re-checks https on EVERY redirect hop (INV-9).

    urllib's default redirect handler would transparently follow a 3xx to http://, so guarding
    only the first URL is not enough.

    CA trust is certifi when it is importable, else the system store (D7). The frozen binaries
    are built on one distro and run on any, and a host whose CA bundle sits somewhere the
    frozen OpenSSL does not look yields no CAs at all -- which INV-13 would then swallow as a
    silent "no update". A source checkout has no such problem and needs no extra dependency.
    """
    import ssl
    import urllib.request

    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()

    class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _require_https(newurl)  # raises before the redirect is followed
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx), _HttpsOnlyRedirect()
    )


def fetch_latest_release(owner: str = GITHUB_OWNER, repo: str = GITHUB_REPO) -> dict:
    """GET /repos/{owner}/{repo}/releases/latest and return the parsed JSON.

    /releases/latest excludes prereleases, so an rc tag is never offered. The request carries a
    fixed User-Agent and nothing else -- no query string, no cookie, no identifier (INV-3).
    """
    import urllib.request

    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    _require_https(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "rolodex-updater", "Accept": "application/vnd.github+json"},
    )
    with _opener().open(request, timeout=UPDATE_TIMEOUT_S) as response:
        raw = response.read(MAX_API_BYTES + 1)
    if len(raw) > MAX_API_BYTES:
        raise UpdateError("release API response exceeds the size cap")
    return json.loads(raw.decode("utf-8"))


def download_to(url: str, dest: str, max_bytes: int) -> None:
    """Stream *url* to *dest*, aborting once the running total exceeds *max_bytes* (INV-9).
    Any failure deletes the partial file, so a broken download never leaves bytes behind."""
    import urllib.request

    _require_https(url)
    request = urllib.request.Request(url, headers={"User-Agent": "rolodex-updater"})
    received = 0
    try:
        with _opener().open(request, timeout=UPDATE_TIMEOUT_S) as response, open(dest, "wb") as fh:
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise UpdateError("download exceeds the size cap")
                fh.write(chunk)
    except BaseException:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise


class UpdateInfo:
    """A newer, signed, non-skipped release the user may install (INV-5)."""

    def __init__(self, version, asset_url, sig_url, notes):
        self.version = version
        self.asset_url = asset_url
        self.sig_url = sig_url
        self.notes = notes


def check_for_update(*, force=False, fetcher=None, current_version=None, config_path=None):
    """Return an UpdateInfo to offer, or None. The whole opt-in gate lives here (INV-1/2).

    Order matters. The platform/frozen gate runs FIRST, so an unsupported build never reaches
    the network at all; then the opt-in gate, so a disabled app makes no request on the silent
    startup path. *force* is the manual "Check for updates" action -- an explicit click is its
    own consent, so it bypasses the opt-in gate and NOTHING else: the version compare, the skip
    and the asset predicate all still apply.

    Failures are swallowed to None on the silent path and RAISED under force (INV-13). None
    alone conflates "up to date" with "could not check", and a button the user pressed on
    purpose must not answer a DNS failure with "You're up to date".
    """
    if not is_update_supported():
        return None
    if not force and not update_check_enabled(config_path):
        return None
    fetch = fetcher or fetch_latest_release
    try:
        current = parse_version(current_version or __version__)
        if current is None:
            return None
        release = fetch()
        latest = parse_version(release.get("tag_name") or "")
        if latest is None or not version_gt(latest, current):
            return None
        version = version_string(release.get("tag_name") or "")
        if version == update_skipped_version(config_path):
            return None
        urls = select_update_assets(release.get("assets") or [], platform_asset_name())
        if urls is None:
            return None
        return UpdateInfo(version, urls[0], urls[1], release.get("body") or "")
    except Exception as exc:
        if force:
            raise UpdateError(f"could not check for updates: {exc}") from exc
        return None


def download_and_verify(info, *, downloader=None, target=None) -> str:
    """Download the asset and its .sig, verify Ed25519 over the exact bytes, return a temp path.

    Staged in the target binary's OWN directory so the eventual install is a same-filesystem
    os.replace (INV-10). On any failure every temp is removed and the running binary is left
    byte-for-byte intact. A bad signature raises UpdateVerificationError; everything else
    raises UpdateError (INV-8).
    """
    from cryptography.exceptions import InvalidSignature

    target = target or detect_installer()
    if target is None:
        raise UpdateError("self-update is not supported on this build")
    fetch = downloader or download_to
    directory = os.path.dirname(os.path.abspath(target))
    asset_tmp = sig_tmp = None
    try:
        fd, asset_tmp = tempfile.mkstemp(dir=directory, prefix=".rolodex-update-")
        os.close(fd)
        fd, sig_tmp = tempfile.mkstemp(dir=directory, prefix=".rolodex-update-", suffix=".sig")
        os.close(fd)
        fetch(info.asset_url, asset_tmp, MAX_UPDATE_BYTES)
        fetch(info.sig_url, sig_tmp, MAX_SIG_BYTES)
        with open(asset_tmp, "rb") as fh:
            data = fh.read()
        with open(sig_tmp, "rb") as fh:
            signature = fh.read()
        try:
            release_public_key().verify(signature, data)
        except InvalidSignature as exc:
            raise UpdateVerificationError("the update's signature did not verify") from exc
        os.unlink(sig_tmp)
        sig_tmp = None
        return asset_tmp
    except BaseException as exc:
        for tmp in (asset_tmp, sig_tmp):
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        # INV-8/INV-13: the only caller catches UpdateError and UpdateVerificationError and
        # nothing else, so anything escaping as its own type would kill the worker thread with
        # a stderr traceback and leave the "Downloading..." toast simply ending. mkstemp raises
        # OSError on a read-only or full directory, and download_to re-raises URLError /
        # HTTPError / SSLError / TimeoutError verbatim. Convert them; let control-flow through.
        if isinstance(exc, (UpdateError, KeyboardInterrupt, SystemExit)):
            raise
        raise UpdateError(f"could not stage the update: {exc}") from exc


def _relaunch_env() -> dict:
    """The environment for the relaunch waiter (D9).

    PYINSTALLER_RESET_ENVIRONMENT=1 is PyInstaller's supported restart signal: it makes the new
    one-file bootloader treat itself as a fresh top-level instance and re-extract, instead of
    assuming it is a worker subprocess of the old one and reusing an extraction dir that is
    being deleted.

    The loader vars matter just as much. A frozen app runs with LD_LIBRARY_PATH pointing at its
    private _MEI dir so it finds its bundled libraries; inherited by /bin/sh, the system shell
    then loads those bundled libraries and can die on a symbol lookup before it ever relaunches.
    PyInstaller preserves the pre-launch value in <VAR>_ORIG, so restore from that, or drop the
    variable where there was none.
    """
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD", "DYLD_LIBRARY_PATH"):
        original = env.pop(f"{var}_ORIG", None)
        if original:
            env[var] = original
        else:
            env.pop(var, None)
    return env


def _relaunch_command(binary: str, pid: int) -> list:
    """A detached /bin/sh that waits for the OLD process to exit, then execs the new binary.

    Launching the replacement before the old process has torn down is a real bug rather than a
    theoretical one: the fresh bootloader collides with the old _MEI extraction dir and dies.
    The wait is hard-capped so a wedged old process cannot hang the relaunch forever. The path
    is shlex.quote-d, and it is our own sys.executable rather than user input.
    """
    import shlex

    quoted = shlex.quote(binary)
    return [
        "/bin/sh",
        "-c",
        f"i=0; while kill -0 {pid} 2>/dev/null; do "
        f'i=$((i+1)); [ "$i" -ge 600 ] && break; sleep 0.1; done; exec {quoted}',
    ]


def apply_update(new_file: str, *, target=None, on_before_exec=None):
    """Swap the verified download into place and relaunch, replacing this process (INV-14).

    chmod then os.replace: any failure before the replace completes leaves the running binary
    byte-for-byte intact, so the temp is dropped and the error surfaced with nothing installed.
    Once the swap HAS committed we never return into a live window whose binary changed
    underneath it -- if the relaunch spawn fails we still exit, because the new binary is
    already in place and a manual restart gets the new version.
    """
    target = target or detect_installer()
    if target is None:
        raise UpdateError("self-update is not supported on this build")
    try:
        os.chmod(new_file, 0o755)
        os.replace(new_file, target)
    except OSError as exc:
        try:
            os.unlink(new_file)
        except OSError:
            pass
        raise UpdateError(f"could not install the update: {exc}") from exc
    if on_before_exec is not None:
        on_before_exec()
    try:
        subprocess.Popen(
            _relaunch_command(str(target), os.getpid()),
            env=_relaunch_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        pass  # swap already committed; exit anyway so a manual restart gets the new version
    os._exit(0)


def sweep_stale_update_temps(target=None) -> int:
    """Delete orphaned update temps left by a download the process did not outlive (INV-15).

    download_and_verify stages into the target binary's own directory and unlinks on its way
    out, but the worker is a daemon thread: quitting mid-download freezes it at interpreter
    finalisation, so that cleanup never runs and a partial asset of up to MAX_UPDATE_BYTES is
    left behind. Nothing else ever removes one, so sweep at startup. Only files older than a
    day are touched, so a download running in another instance is never pulled out from under
    it. Returns the number removed; never raises -- a failure here must not block startup.
    """
    target = target or detect_installer()
    if target is None:
        return 0
    directory = os.path.dirname(os.path.abspath(str(target)))
    cutoff = time.time() - 86400
    removed = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(".rolodex-update-"):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
                removed += 1
        except OSError:
            continue
    return removed


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
                vault, salt, key = create_vault_with_key(pw, self.vault_path)
            except Exception as e:
                self._show_error(str(e))
                return
            self.app.open_main(vault, salt, pw, self.vault_path, key)
            self.close()
        else:
            self.btn.set_sensitive(False)
            self.btn.set_label("Unlocking...")
            # Run decryption in a thread so the UI doesn't freeze
            import threading
            threading.Thread(target=self._try_unlock, args=(pw,), daemon=True).start()

    def _try_unlock(self, pw):
        try:
            vault, salt, key = load_vault_with_key(pw, self.vault_path)
            GLib.idle_add(self._unlock_ok, vault, salt, pw, key)
        except InvalidToken:
            GLib.idle_add(self._unlock_fail, "Wrong password.")
        except Exception as e:
            GLib.idle_add(self._unlock_fail, str(e))

    def _unlock_ok(self, vault, salt, pw, key):
        # Everything after a SUCCESSFUL decrypt needs its own handler. This runs from a
        # GLib.idle_add callback with the unlock button already disabled, so an exception here
        # (a malformed vault reaching migrate_vault, a bad value in .rolodex.conf reaching
        # MainWindow.__init__) froze the dialog on "Unlocking..." forever, with the vault
        # decrypted in memory and nothing on screen explaining why.
        try:
            migrate_vault(vault)
            self.app.open_main(vault, salt, pw, self.vault_path, key)
        except Exception as exc:  # noqa: BLE001 - last resort; the alternative is a frozen dialog
            self._unlock_fail(f"The vault opened but could not be loaded: {exc}")
            return
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
        # Find the MainWindow ancestor. Report the real outcome: returning True unconditionally
        # told GTK the drop succeeded even when the row had been rebuilt out from under the drag
        # and get_root() no longer resolved, so the entry silently did not move.
        widget = self.get_root()
        if not isinstance(widget, MainWindow):
            return False
        widget._move_entry_to_category(dragged_row.entry_id, self.category_name)
        return True


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, vault, salt, password, vault_path, key=None):
        super().__init__(application=app, title="Rolodex")
        self.app_ref = app
        self.vault = vault
        self.salt = salt
        self.password = password
        # The derived key for (password, salt), held for the session so that saving does not
        # re-run the KDF on the UI thread (ROLO-0043). Unlock and create already derived it and
        # pass it in; it is derived here only if they did not. It is the master password in
        # another form, so it is cleared everywhere self.password is, and re-derived only where
        # the salt rotates -- _finish_change_password and _finish_restore.
        self._key = key if key is not None else derive_key(password, salt)
        self.vault_path = vault_path
        self._revealed = False
        # TOTP live-code tick (ROLO-0006): one 1s timer refreshes every code row on screen.
        self._totp_tick_id = None
        self._totp_widgets = []

        # Restore saved window size or use defaults
        conf = load_config()
        w = config_int(conf, "window_width", 820)
        h = config_int(conf, "window_height", 580)
        self.set_default_size(w, h)
        if conf.get("window_maximized"):
            self.maximize()

        # Security timeouts (0 disables either). Read once at unlock; edit .rolodex.conf to change.
        self._clipboard_clear_s = config_int(
            conf, "clipboard_clear_seconds", DEFAULT_CLIPBOARD_CLEAR_SECONDS
        )
        self._idle_timeout_s = config_int(conf, "idle_lock_seconds", DEFAULT_IDLE_LOCK_SECONDS)
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
        menu.append("Check for updates...", "win.check-updates")
        menu.append("Check for updates automatically", "win.auto-updates")
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
            ("check-updates", self._on_check_updates),
        ]:
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            self.add_action(action)

        # "Move to category" action for right-click context menu
        move_action = Gio.SimpleAction(name="move-to-category", parameter_type=GLib.VariantType.new("(ss)"))
        move_action.connect("activate", self._on_move_to_category_action)
        self.add_action(move_action)

        # Automatic-update-check toggle (ROLO-0037). A STATEFUL action, so the menu renders it
        # as a checkbox — this is the only in-app way to set `check_for_updates`, and without
        # it the preference could be changed only by hand-editing .rolodex.conf.
        auto_update_action = Gio.SimpleAction.new_stateful(
            "auto-updates", None, GLib.Variant.new_boolean(update_check_enabled())
        )
        auto_update_action.connect("change-state", self._on_toggle_auto_updates)
        auto_update_action.set_enabled(is_update_supported())
        self.add_action(auto_update_action)

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

        # The SILENT startup check (ROLO-0037, INV-1). This is the path the
        # `check_for_updates` preference actually gates -- without it the preference would be
        # inert and the feature would only ever check when explicitly clicked. Deferred a few
        # seconds so it never competes with showing the window, and it stays silent: a failure
        # yields None and no dialog (INV-13).
        #
        # INV-15 teardown state. _update_cancelled is set by _lock and _on_close_request; both
        # _update_worker and _install_update check it, so a download completing after the vault
        # is locked deletes its temp instead of swapping the binary out from under the unlock
        # screen. _silent_check_id is tracked so the deferred check cannot fire against a window
        # that closed inside the three seconds.
        self._update_cancelled = False
        self._silent_check_id = 0
        self._clipboard_timer_id = 0
        self._clipboard_pending_value = None
        self._rebuilding = False
        if update_check_enabled() and is_update_supported():
            self._silent_check_id = GLib.timeout_add_seconds(3, self._start_silent_update_check)

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
        # CAPTURE, matching UnlockDialog: in the default BUBBLE phase a key consumed by the
        # focused GtkText (the search box, every dialog entry, the notes view) never reaches
        # this handler, so typing did not reset the activity clock and only mouse motion did.
        # A user composing a long note was then locked out mid-edit, losing the open dialog.
        keyctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keyctl.connect("key-pressed", self._bump_activity)
        self.add_controller(keyctl)
        self._start_idle_timer()

    # ------------------------------------------------------------------
    # Vault persistence
    # ------------------------------------------------------------------

    def _save(self) -> bool:
        """Re-encrypt and write the whole vault, surfacing a write failure. True on success.

        A failed write used to escape into the GTK signal handler: PyGObject printed a traceback
        and carried on, so the UI reported success while the in-memory vault silently diverged
        from disk -- and _lock's "nothing unsaved to lose" comment relied on that being
        impossible. Callers that must roll back on failure (the password change, the restore)
        call save_vault directly instead, so they can order the write before the assignment.
        """
        try:
            save_vault_with_key(self.vault, self._key, self.salt, self.vault_path)
            return True
        except OSError as exc:
            self._show_message("Could Not Save", f"The vault was not written to disk: {exc}")
            return False

    def _on_close_request(self, *_args):
        self._cancel_search_debounce()
        self._cancel_totp_tick()  # covers _lock too, which routes through close()
        self._cancel_pending_update()
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

        # Clear list. gtk_list_box_remove emits ::row-selected(NULL) for the selected row, so
        # _on_row_selected would otherwise wipe _current_entry_id on every rebuild -- before the
        # re-selection pass below ever gets to look for it (search.md INV-7).
        self._rebuilding = True
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

        # Rebuild finished: from here the select_row() calls below are deliberate, so the
        # handler must see them.
        self._rebuilding = False
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
            # Entry filtered out or inside a collapsed category. Blank the detail pane but KEEP
            # _current_entry_id, so clearing the search or expanding the category re-selects it
            # (search.md INV-7). Clearing it here is what broke that: typing until the selected
            # entry dropped out of the results discarded the selection permanently.
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
        if self._rebuilding:
            return  # a teardown/rebuild artefact, not a user action
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
            is_sensitive = field_is_sensitive(field)
            if is_sensitive and not self._revealed:
                display = MASK
            else:
                display = field["value"]

            val_label = Gtk.Label(label=display)
            val_label.set_selectable(True)
            if is_sensitive and not self._revealed:
                val_label.add_css_class("field-masked")
            elif is_sensitive and self._revealed:
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
        # Remember what was copied whatever the delay: with clipboard_clear_seconds = 0 there is
        # no timer at all, and a lock still has to be able to wipe it (ROLO-0003).
        self._cancel_clipboard_timer()
        self._clipboard_pending_value = value
        if delay > 0:
            self._toast(f"Copied {label} — clipboard clears in {delay}s")
            self._clipboard_timer_id = GLib.timeout_add_seconds(
                delay, self._clear_clipboard_if_unchanged, value
            )
        else:
            self._toast(f"Copied {label}")

    def _make_copy_handler(self, value, label):
        return lambda _btn: self._copy_value(value, label)

    def _clear_clipboard_if_unchanged(self, value):
        """Wipe the clipboard, but only if it still holds the secret we copied (ROLO-0003)."""
        self._clipboard_timer_id = 0  # this source removes itself via the False returns below
        current = read_clipboard()
        # If a reader is available and the clipboard has moved on, leave the user's new copy
        # alone. wl-copy --trim-newline stores a trailing-newline value trimmed, so compare
        # against the trimmed form too -- otherwise such a value never matches and the secret
        # stays on the clipboard for good.
        if current is not None and current != value and current != value.rstrip("\n"):
            self._clipboard_pending_value = None
            return False
        # No reader available falls through to the wipe deliberately: for a credential manager,
        # clearing a clipboard we cannot inspect is the safe direction.
        copy_to_clipboard("")
        self._clipboard_pending_value = None
        return False  # one-shot timeout

    def _cancel_clipboard_timer(self):
        if self._clipboard_timer_id:
            GLib.source_remove(self._clipboard_timer_id)
            self._clipboard_timer_id = 0

    def _clear_clipboard_on_lock(self):
        """A lock must not leave a copied secret on the clipboard.

        The auto-clear timer alone cannot cover this: it may still be pending, and with
        clipboard_clear_seconds = 0 -- a documented setting -- there is no timer at all, so
        without this the secret would sit there indefinitely.
        """
        pending = self._clipboard_pending_value
        self._cancel_clipboard_timer()
        if pending is not None:
            self._clear_clipboard_if_unchanged(pending)

    def _cancel_pending_update(self):
        """INV-15: tear down anything the update path has in flight.

        The worker thread cannot be killed, so it is told to abandon its result instead: it and
        _install_update both read _update_cancelled, and whichever reaches the staged file first
        unlinks it.
        """
        self._update_cancelled = True
        if self._silent_check_id:
            GLib.source_remove(self._silent_check_id)
            self._silent_check_id = 0

    # --- Opt-in signed auto-update (ROLO-0037) --------------------------------------------

    def _on_toggle_auto_updates(self, action, value):
        """Turn the automatic check on or off (INV-1). The only in-app writer of the preference."""
        enabled = bool(value.get_boolean())
        set_update_check_enabled(enabled)
        action.set_state(value)
        self._toast(
            "Rolodex will check for updates on startup"
            if enabled
            else "Automatic update checks turned off"
        )

    def _start_silent_update_check(self):
        """Kick off the startup check on a background thread. Returns False so the GLib
        timeout does not repeat."""
        import threading

        self._silent_check_id = 0  # this source removes itself via the False return below
        threading.Thread(target=self._silent_update_worker, daemon=True).start()
        return False

    def _silent_update_worker(self):
        """The unforced check. Every failure yields None and is never surfaced (INV-13) --
        this path runs without the user asking, so it must never interrupt them."""
        info = check_for_update()
        if info is not None:
            GLib.idle_add(self._offer_update, info)

    def _offer_update(self, info):
        UpdateDialog(self, info).present(self)
        return False

    def _on_check_updates(self, *_):
        """The manual "Check for updates..." action (INV-6).

        An explicit click is its own consent, so this runs with force=True even when the
        preference is off. Off an unsupported build it says so rather than silently doing
        nothing (INV-2). The check runs on a background thread so a slow or hanging network
        never freezes the UI -- the same pattern the unlock path uses (INV-15).
        """
        if not is_update_supported():
            self._show_message(
                "Updates not available",
                "In-app updates work only in the packaged Rolodex build. Running from source "
                "or from a distribution package, updating is handled outside the app.",
            )
            return
        import threading

        self._toast("Checking for updates...")
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_worker(self):
        """Background half of the manual check. Never touches GTK directly."""
        try:
            info = check_for_update(force=True)
        except UpdateError as exc:
            # INV-13: a forced failure must not read as "you're up to date".
            GLib.idle_add(self._toast, f"Couldn't check for updates: {exc}")
            return
        GLib.idle_add(self._finish_check_updates, info)

    def _finish_check_updates(self, info):
        if info is None:
            self._toast(f"Rolodex {__version__} is up to date")
            return
        self._offer_update(info)

    def _start_update_download(self, info):
        """Download and verify on a background thread, then install (INV-8/INV-15)."""
        import threading

        self._toast(f"Downloading Rolodex {info.version}...")
        threading.Thread(target=self._update_worker, args=(info,), daemon=True).start()

    def _update_worker(self, info):
        try:
            staged = download_and_verify(info)
        except UpdateVerificationError:
            GLib.idle_add(
                self._show_message,
                "Update rejected",
                "The downloaded update was not signed by the Rolodex release key, so it was "
                "discarded and nothing was installed. Your current version is untouched.",
            )
            return
        except UpdateError as exc:
            GLib.idle_add(self._show_message, "Update failed", str(exc))
            return
        # INV-15: the window may have been locked or closed while this ran. Drop the download
        # rather than installing something the user is no longer consenting to.
        if self._update_cancelled:
            try:
                os.unlink(staged)
            except OSError:
                pass
            return
        GLib.idle_add(self._install_update, staged)

    def _install_update(self, staged):
        """Swap and relaunch. apply_update does not return -- it replaces this process.

        Re-checks the cancel flag (INV-15): this runs from an idle callback, so the vault can
        have been locked between _update_worker's own check and this call. Installing then would
        swap the binary and relaunch underneath the unlock screen.
        """
        if self._update_cancelled:
            try:
                os.unlink(staged)
            except OSError:
                pass
            return
        try:
            apply_update(staged, on_before_exec=self._wipe_secrets_for_update)
        except UpdateError as exc:
            self._show_message("Update failed", str(exc))

    def _wipe_secrets_for_update(self):
        """Drop the in-memory password and vault before the process is replaced.

        The relaunch exits via os._exit, so no GTK teardown or destructor runs -- anything
        that must be cleared has to be cleared here.
        """
        self.password = None
        self.vault = None
        self.salt = None
        self._key = None

    def _toast(self, msg):
        # AdwToast:use-markup defaults to TRUE, so this is a Pango markup sink and callers
        # interpolate raw field labels into it. A label of "AT&T" or "<work>" produced a parse
        # failure and a toast that rendered wrong or not at all. security-standards.md requires
        # escaping at every markup sink.
        self._toast_overlay.add_toast(
            Adw.Toast(title=GLib.markup_escape_text(str(msg)), timeout=2)
        )

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
                      if field_is_sensitive(f)), None)
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
        self._cancel_pending_update()
        self._clear_clipboard_on_lock()
        # Drop the rendered entry as well: detail_box holds the last-viewed values as label text
        # and one copy closure per field, so clearing self.vault alone leaves them reachable.
        clear_container(self.detail_box)
        self.detail_stack.set_visible_child_name("empty")
        self._current_entry_id = None
        # Wipe secrets from memory before showing the lock screen. Every mutation saves via
        # _save(), which now surfaces a write failure rather than letting it escape -- so
        # anything still unsaved at this point has already been reported to the user.
        self.vault = None
        self.salt = None
        self.password = None
        self._key = None
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

        if self.vault is None:
            return  # the idle lock fired while the file dialog held the input grab

        try:
            parsed = parse_text_file(filepath)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            self._show_message("Import Error", str(e))
            return

        if not parsed:
            self._show_message("Import", "No entries found in file.")
            return

        # Show preview dialog
        dialog = ImportPreviewDialog(self, parsed, filepath)
        dialog.present(self)

    def _finish_import(self, parsed):
        if self.vault is None:
            return  # locked while the preview dialog was open
        imported, skipped = import_entries(self.vault, parsed)
        self._save()
        self._refresh_list()
        msg = f"Imported {imported} {entries_noun(imported)}."
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
        # write_private_file rather than copy2 + chmod: copyfile creates the destination through
        # open(dst, 'wb'), i.e. 0644 under the usual umask, and writes the whole ciphertext
        # before chmod narrows it -- a real window for a local reader, against
        # security-standards.md's "created 0600". It also truncates the destination first, so an
        # interrupted backup over a previous good one destroyed it; write_private_file stages a
        # temp and os.replace()s, so the old backup survives a failure intact.
        try:
            with open(self.vault_path, "rb") as fp:
                blob = fp.read()
            write_private_file(filepath, blob)
            self._toast("Vault backed up")
        except OSError as e:
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
        if self.vault is None:
            return  # the idle lock fired while the file dialog held the input grab
        # Prompt for the backup's master password
        self._restore_path = filepath
        pw_dialog = RestorePasswordDialog(self)
        pw_dialog.present(self)

    def _finish_restore(self, vault, salt, password, key):
        if self.vault is None:
            return  # the vault was locked while the file dialog was open
        migrate_vault(vault)  # INV-13: migrate before the backup becomes live
        # Write first, adopt second -- the same ordering as the password change and for the same
        # reason: a failed write would leave the session holding the backup's credentials while
        # contacts.vault still held the original, and the next edit's save would then overwrite
        # the original with a restore the user had been told did not happen.
        try:
            save_vault_with_key(vault, key, salt, self.vault_path)
        except OSError as exc:
            self._show_message(
                "Restore Failed",
                f"The backup could not be written to the vault, so nothing changed: {exc}",
            )
            return
        self.vault = vault
        self.salt = salt
        self.password = password
        self._key = key
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
        if self.vault is None:
            return  # the idle lock fired while the file dialog held the input grab

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
        """Rotate the salt and re-encrypt under the new password (INV-11).

        The write comes FIRST and the session state is adopted only once it has landed. The
        other order rotated self.password and self.salt unconditionally, so a failed write left
        the session holding credentials the on-disk vault did not use: the change looked like it
        had not taken, and the next successful save from any edit then silently re-encrypted the
        vault under a password the user may never have written down. There is no recovery path.
        """
        new_salt = os.urandom(16)
        new_key = derive_key(new_pw, new_salt)
        try:
            save_vault_with_key(self.vault, new_key, new_salt, self.vault_path)
        except OSError as exc:
            self._show_message(
                "Password Not Changed",
                f"The vault could not be written, so your master password is unchanged: {exc}",
            )
            return
        self.password = new_pw
        self.salt = new_salt
        self._key = new_key
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
        # Also unparent if the row is disposed first: _refresh_list() can rebuild the sidebar
        # from another source (the idle lock, the search debounce) while this menu is open,
        # destroying the parent row out from under a live popover child.
        entry_row.connect("destroy", lambda _r, p=popover: p.unparent() if p.get_parent() else None)
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

        # Latches once the user toggles "Hide" by hand, after which the label no longer drives
        # sensitivity (see on_label_changed).
        self._sens_user_set = False

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
            # The user has now decided this field's sensitivity by hand; stop re-deriving it
            # from the label (see on_label_changed).
            self._sens_user_set = True
            self._peek = False
            self.gen_btn.set_visible(check.get_active())
            self._update_value_visibility()
        self.sens_check.connect("toggled", on_sens_toggled)

        # Auto-check "Hide" when the label gains a sensitive keyword (one-way; the user can
        # un-check manually). Removing the keyword leaves the checkbox as-is.
        def on_label_changed(entry):
            # Auto-detect only until the user overrides it. This fires on every KEYSTROKE, not
            # on a keyword transition, so without the latch: un-tick "Hide" on a field labelled
            # "Password", then fix a typo anywhere in that label, and it silently re-ticked.
            # INV-10 promises the override works in both directions.
            if self._sens_user_set:
                return
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
        # Tell the platform this is a secret. Without these an input method may keep it in
        # candidate/history state that outlives the process, and spellcheck and the emoji picker
        # stay live over a vault password -- none of which set_visibility(False) prevents.
        if sensitive:
            self.value_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            self.value_entry.set_input_hints(
                Gtk.InputHints.PRIVATE | Gtk.InputHints.NO_SPELLCHECK | Gtk.InputHints.NO_EMOJI
            )
        else:
            self.value_entry.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
            self.value_entry.set_input_hints(Gtk.InputHints.NONE)
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
            # The generator button is only visible when "Hide" is already on, so set_active(True)
            # is a no-op and the toggled handler never runs -- which left _peek set, so a
            # password generated while peeking stayed on screen in cleartext. Reset it here.
            self._peek = False
            self._update_value_visibility()
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
                row = FieldRow(self, field["label"], field["value"], field_is_sensitive(field))
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
            # The value is stored VERBATIM. Stripping it silently altered any secret with a
            # meaningful leading or trailing space, with no warning and no way to express one --
            # and _snapshot() compares the unstripped text, so the dirty check and the commit
            # disagreed about what the form held. Only the emptiness test trims.
            value = row.value_entry.get_text()
            if label or value.strip():
                fields.append({
                    "label": label or "Unlabeled",
                    "value": value,
                    # A recognised TOTP seed is stored sensitive whatever the checkbox says --
                    # see field_is_sensitive() for why the label keywords cannot decide this.
                    "sensitive": row.sens_check.get_active()
                    or parse_totp_field(label, value) is not None,
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
            vault, salt, key = load_vault_with_key(pw, self.main_win._restore_path)
            GLib.idle_add(self._unlock_ok, vault, salt, pw, key)
        except InvalidToken:
            GLib.idle_add(self._unlock_fail, "Wrong password for this backup.")
        except Exception as e:
            GLib.idle_add(self._unlock_fail, str(e))

    def _unlock_ok(self, vault, salt, pw, key):
        # The KDF runs on a background thread and Cancel/Esc only closes this dialog -- it does
        # not cancel or disown the thread. Without this check a cancelled restore still landed
        # and overwrote the live vault, which is the one operation here that cannot be undone.
        if not self.get_presented():
            return
        self.main_win._finish_restore(vault, salt, pw, key)
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
        if dragged_row.cat_name not in cats or target_row.cat_name not in cats:
            return  # stale row reference (matches _reorder_field's guard)
        old_idx = cats.index(dragged_row.cat_name)
        target_idx = cats.index(target_row.cat_name)
        cats.pop(old_idx)
        new_idx = cats.index(target_row.cat_name)
        # Dropping onto a row BELOW the dragged one inserts after it. Computing the index after
        # the pop and always inserting before the target made the last slot unreachable by drag,
        # and there is no other way to reorder categories.
        if old_idx < target_idx:
            new_idx += 1
        cats.insert(new_idx, dragged_row.cat_name)
        self.main_win._save()
        self._rebuild_list()
        self.main_win._refresh_list()


# --------------------------------------------------------------------------
# Update prompt (ROLO-0037)
# --------------------------------------------------------------------------


class UpdateDialog(Adw.AlertDialog):
    """Offers a verified update: Later / Skip this version / Update now (INV-7).

    Deliberately not auto-installing. A password manager replacing its own binary unattended
    is a lot of trust for a little convenience, and it removes the user's chance to read what
    changed.
    """

    def __init__(self, main_win, info):
        # info.notes is the GitHub release body: fetched over TLS but NOT covered by the Ed25519
        # signature, which per INV-8 protects only the asset bytes. So this is unauthenticated
        # remote text rendering inside a native-looking dialog in a password manager. Strip the
        # control and bidi-override characters that would let it disguise itself as app UI --
        # markup is already off, since AdwAlertDialog:body-use-markup defaults FALSE.
        # str.isprintable() is False for every Cf format character, which is the whole
        # bidi-override family (U+202A-U+202E, U+200E/F, U+2066-U+2069) as well as the C0/C1
        # controls -- so it covers the attack on its own. An explicit range here would have to
        # spell those codepoints out, and writing them as literals puts real bidi overrides into
        # this source file: the Trojan Source hazard, introduced by the guard against it.
        notes = "".join(
            ch for ch in (info.notes or "") if ch in "\n\t" or ch.isprintable()
        ).strip()
        if len(notes) > 1500:
            notes = notes[:1500].rstrip() + "\n\n(...)"
        super().__init__(
            heading=f"Rolodex {info.version} is available",
            body=(f"You have {__version__}.\n\n{notes}" if notes else f"You have {__version__}."),
        )
        self.main_win = main_win
        self.info = info
        self.add_response("later", "Later")
        self.add_response("skip", "Skip This Version")
        self.add_response("update", "Update Now")
        self.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("later")
        self.set_close_response("later")
        self.connect("response", self._on_response)

    def _on_response(self, _dialog, response):
        # "Later" persists nothing, by design (INV-7).
        if response == "skip":
            skip_update_version(self.info.version)
            self.main_win._toast(f"Skipping Rolodex {self.info.version}")
        elif response == "update":
            self.main_win._start_update_download(self.info)


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
        # INV-15: remove update temps orphaned by a download whose process did not outlive it.
        sweep_stale_update_temps()
        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(CUSTOM_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def do_activate(self):
        # Single-instance app (FLAGS_NONE): a second launch delivers activate() to the running
        # process rather than starting a new one. Without this guard that built a fresh
        # UnlockDialog over the live window, and unlocking it created a SECOND MainWindow --
        # two owners of persistence, each saving the whole vault, so edits made in one were
        # silently destroyed by the next save from the other.
        existing = self.props.active_window
        if existing is not None:
            existing.present()
            return
        is_new = not os.path.exists(self.vault_path)
        win = UnlockDialog(self, self.vault_path, is_new)
        win.present()

    def open_main(self, vault, salt, password, vault_path, key=None):
        win = MainWindow(self, vault, salt, password, vault_path, key)
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
    # Propagate the exit status: discarding it meant a GApplication startup failure still exited
    # 0, so a wrapper script or a CI step could not tell that the app never started.
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
