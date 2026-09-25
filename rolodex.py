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
from typing import IO, TYPE_CHECKING, Any, Callable, ClassVar, Collection, Iterable, NoReturn

if TYPE_CHECKING:  # for annotations only: INV-12 keeps these network modules out at runtime
    import ssl
    import urllib.request

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

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
__version__ = "1.6.0"

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

# ROLO-0026: .rolodex.conf key for the entry to reopen on (an entry id, never a name).
LAST_ENTRY_KEY = "last_entry_id"

# ROLO-0050: the largest text file the importer will read. Far above any real credential
# export, and far below what would exhaust memory.
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ENTRIES = 2000  # ROLO-0070: rows the import preview will build in one go
_IMPORT_TOO_LARGE = "That file is too large to import (the limit is 10 MB)."

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
    # A symlinked destination is written THROUGH, to the file it points at (ROLO-0078). 1.3.1's
    # move to os.replace had started replacing the link itself with a regular file, silently
    # detaching a vault kept as a link into a synced folder. The temp is staged beside the real
    # file, so the replace stays same-filesystem.
    path = os.path.realpath(path)
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


class VaultBusyError(Exception):
    """Another Rolodex process holds this vault unlocked (ROLO-0044)."""


class VaultChangedError(Exception):
    """The vault on disk changed since this session read or last wrote it (ROLO-0044)."""


def vault_fingerprint(path: str) -> tuple[int, int, int] | None:
    """What the session last saw of the vault file: (inode, size, mtime_ns), or None if absent.

    Compared before every write. A different value means something else -- a second copy of
    Rolodex, a sync client, a restore by hand -- wrote the file, and saving the whole in-memory
    vault over it would silently destroy that write (ROLO-0044). os.stat follows a symlink, so
    this fingerprints the real file (ROLO-0078).
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


class VaultLock:
    """An exclusive advisory lock on a vault, held for as long as the vault is unlocked.

    The lock is taken on a `<vault>.lock` sidecar, never on the vault itself: os.replace swaps
    the vault's inode on every save, so a lock on the vault file would stop covering it after
    the first write. The sidecar is never deleted -- unlinking a lock file is how two processes
    come to lock two different files. The lock follows the real path, so two symlinks to one
    vault share one lock. A lock does not reach across a network or synced folder; that case is
    what vault_fingerprint's check on every save is for (ROLO-0044).
    """

    def __init__(self, vault_path: str) -> None:
        self.path = os.path.realpath(vault_path) + ".lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        """Take the lock or raise VaultBusyError. Safe to call when already held."""
        if self._fd is not None:
            return
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock_fd(fd)
        except OSError:
            os.close(fd)
            raise VaultBusyError("This vault is already open in another Rolodex window.") from None
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing the descriptor drops the lock
            self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None


def _lock_fd(fd: int) -> None:
    """Non-blocking exclusive lock on *fd*; raises OSError when another process holds it."""
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


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
    """Create an empty vault, returning its derived key alongside (see load_vault_with_key).

    Refuses when a vault already exists. Whether to create was decided when the unlock screen
    opened, and the write is an unconditional os.replace, so a vault that appeared in between
    -- restored by hand, synced down -- was destroyed (ROLO-0044).
    """
    if os.path.exists(path):
        raise FileExistsError("A vault already exists here. Restart Rolodex to unlock it.")
    salt = os.urandom(16)
    key = derive_key(password, salt)
    vault_data = {"version": 2, "categories": [], "entries": {}}
    save_vault_with_key(vault_data, key, salt, path)
    return vault_data, salt, key


def create_vault(password: str, path: str) -> tuple[dict, bytes]:
    vault_data, salt, _key = create_vault_with_key(password, path)
    return vault_data, salt


def set_aside_vault(path: str) -> str | None:
    """Rename an unreadable vault out of the way, returning its new path (None if absent).

    Never deletes: a vault that will not open may still be recoverable by hand, and it may be
    the user's only copy. The new name sits beside the old one, timestamped (ROLO-0045).
    """
    if not os.path.exists(path):
        return None
    real = os.path.realpath(path)
    aside = f"{real}.unreadable-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}"
    os.replace(real, aside)
    return aside


def adopt_vault_file(source: str, path: str) -> str | None:
    """Make the vault file at *source* -- a backup, or an existing vault -- the vault at *path*.

    Checks only the header (MAGIC and a full salt); the password is checked by the normal
    unlock that follows, so this never needs it. Any vault already at *path* is set aside
    first, and its new path is returned (ROLO-0045, ROLO-0078).
    """
    with open(source, "rb") as fp:
        data = fp.read(MAX_IMPORT_BYTES * 10 + 1)
    if len(data) > MAX_IMPORT_BYTES * 10:
        raise ValueError("That file is too large to be a Rolodex vault.")
    if data[:4] != MAGIC or len(data) < 4 + 16 + 1:
        raise ValueError("That file is not a Rolodex vault.")
    aside = set_aside_vault(path)
    write_private_file(path, data)
    return aside


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
    # categories.md INV-1 says names are unique and non-empty. A hand-edited or legacy list
    # holding "" or a repeat made _refresh_list draw that header and its entries twice
    # (ROLO-0070); keep the first of each, in order.
    raw = vault["categories"] if isinstance(vault["categories"], list) else []
    vault["categories"] = list(dict.fromkeys(c for c in raw if isinstance(c, str) and c))
    for entry in vault["entries"].values():
        if not isinstance(entry, dict):
            raise ValueError("Vault contents are not a valid vault")
        if "category" not in entry:
            entry["category"] = ""
        # A legacy or hand-edited vault can lack a key that the editor, search and the password
        # audit index directly, which surfaced as a dead Edit button rather than an error
        # (ROLO-0071). Filling the gaps here -- once, at load -- is what lets every reader stay
        # simple. Nothing present is overwritten, so this stays idempotent.
        entry.setdefault("name", "")
        entry.setdefault("notes", "")
        fields = entry.get("fields")
        if not isinstance(fields, list):
            fields = []
        entry["fields"] = [f for f in fields if isinstance(f, dict)]
        for f in entry["fields"]:
            f.setdefault("label", "")
            f.setdefault("value", "")
            f.setdefault("sensitive", is_sensitive_label(str(f["label"])))
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
    """Classify a field label into a category for its colour bar and type icon."""
    label_lower = label.lower()
    for category, keywords in FIELD_CATEGORIES:
        if any(kw in label_lower for kw in keywords):
            return category
    return "other"


# The non-colour half of the field-category cue (ROLO-0016). Colour alone fails WCAG 1.4.1,
# so every category also carries a distinct shape and a name a screen reader can speak. One
# icon per category, never shared: a repeated icon tells two categories apart no better than
# the border colour does.
#
# Two rules picked these names, and both were learnt by rendering the alternatives rather
# than by reading the icon-naming spec.
#
# 1. Every name is one GTK 4 carries INSIDE the library (its own gresource icon set). The
#    icon theme is whatever the desktop supplies — a KDE session hands GTK breeze-dark,
#    which has no x-office-calendar-symbolic at all, so the obvious calendar name renders as
#    a broken-image square. A theme may override any of these with its own drawing; what it
#    cannot do is leave one missing. It also keeps the icons in the frozen build, which
#    bundles GTK but no system icon theme (ROLO-0088).
# 2. The six must stay distinct FROM EACH OTHER under whichever theme draws them, since the
#    shape is the cue. That rules out the semantically obvious pairing: breeze-dark draws
#    both dialog-password and changes-prevent as a padlock, so credential and key became
#    indistinguishable — the exact failure this item exists to fix, in greyscale or not.
#    Checked by rendering the set under breeze-dark and Adwaita.
#
# test_ROLO0016_every_cue_icon_resolves_on_this_theme holds rule 1. Nothing can hold rule 2
# mechanically — no tool compares two drawings — so changing a name here means rendering
# the set again and looking at it.
FIELD_CATEGORY_CUES = {
    "credential": ("dialog-password-symbolic",   "Credential field"),     # key / padlock
    "key":        ("emblem-system-symbolic",     "Key or token field"),   # a cog
    "identity":   ("emoji-people-symbolic",      "Identity field"),       # a face
    "url":        ("network-workgroup-symbolic", "Link field"),           # a network
    "date":       ("emoji-recent-symbolic",      "Date field"),           # a clock
    "other":      ("text-x-generic-symbolic",    "Uncategorised field"),  # a document
}


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
    """Compute the RFC 6238 TOTP code for a raw (base32-decoded) secret at a unix time.

    Raises ValueError on arguments no real configuration produces. Both callers pass a config
    already validated by _parse_otpauth_uri, but this is a public function, and the raw
    failures -- KeyError, ZeroDivisionError, struct.error for a clock before 1970 -- say
    nothing about the cause (ROLO-0068).
    """
    if algorithm not in _TOTP_HASHES:
        raise ValueError(f"unsupported TOTP algorithm {algorithm!r}")
    if digits not in (6, 7, 8):
        raise ValueError("TOTP digits must be 6, 7 or 8")
    if not 1 <= period <= 300:
        raise ValueError("TOTP period must be 1-300 seconds")
    if timestamp < 0:
        raise ValueError("TOTP needs a clock set after 1970")
    counter = int(timestamp) // period
    mac = hmac.new(secret, struct.pack(">Q", counter), _TOTP_HASHES[algorithm]).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def totp_remaining(timestamp: float, period: int = 30) -> int:
    """Seconds left in the current code's window (equals period exactly on a boundary)."""
    return period - int(timestamp) % period


def clock_synchronized() -> bool | None:
    """Whether the system clock is network-synchronised: True, False, or None for unknown.

    RFC 6238 §6 needs prover and validator to agree on time, and a drifted clock makes every
    code wrong in a way that looks like "the site rejected my code" (ROLO-0068). Only an
    explicit "no" is False. A platform without timedatectl, or any failure, is None, and the
    UI shows nothing for None -- a hint that fires on uncertainty would be a false alarm.
    Runs a subprocess, so callers keep it off the GTK main thread.
    """
    if not shutil.which("timedatectl"):
        return None
    try:
        proc = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                              capture_output=True, text=True, timeout=5, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    answer = proc.stdout.strip()
    if proc.returncode != 0 or answer not in ("yes", "no"):
        return None
    return answer == "yes"


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
                "label": f.get("label", ""),
                "strength": score,
                "strength_label": STRENGTH_LABELS[score],
                "reused": reuse_count > 1,
                "reuse_count": reuse_count,
            })
    findings.sort(key=lambda x: (x["strength"], not x["reused"],
                                 x["entry_name"].lower(), x["label"].lower()))
    return findings


def now_iso() -> str:
    """The current time as an ISO-8601 string WITH its UTC offset (ROLO-0048).

    Naive local time let `modified` precede `created` across a DST fall-back or a timezone
    change. Vaults written before this hold naive values and are deliberately left as they
    are -- which zone they were recorded in is unknowable -- so every reader must accept both.
    """
    return datetime.now().astimezone().isoformat()


def add_entry(vault: dict, name: str, fields: list[dict], notes: str = "", category: str = "") -> str:
    entry_id = str(uuid.uuid4())
    now = now_iso()
    vault["entries"][entry_id] = {
        "name": name,
        "category": category,
        "fields": fields,
        "notes": notes,
        "created": now,
        "modified": now,
    }
    return entry_id


def update_entry(vault: dict, entry_id: str, name: str | None = None,
                 fields: list[dict] | None = None, notes: str | None = None,
                 category: str | None = None) -> None:
    entry = vault["entries"][entry_id]
    if name is not None:
        entry["name"] = name
    if fields is not None:
        entry["fields"] = fields
    if notes is not None:
        entry["notes"] = notes
    if category is not None:
        entry["category"] = category
    entry["modified"] = now_iso()


def delete_entry(vault: dict, entry_id: str) -> None:
    del vault["entries"][entry_id]


def _entry_search_texts(entry: dict) -> list[str]:
    """Every searchable string of an entry, lower-cased: name, category, each field's label
    and value (sensitive ones included, search.md INV-3), and notes."""
    texts = [entry["name"], entry.get("category", "")]
    for field in entry["fields"]:
        texts.append(field.get("label", ""))
        texts.append(field.get("value", ""))
    texts.append(entry.get("notes", ""))
    return [t.lower() for t in texts if t]


def entry_category(vault: dict, entry: dict) -> str:
    """The category an entry is shown under: its own, or "" when it has none or names a
    category the vault no longer lists -- the rule entries_by_category applies."""
    cat = entry.get("category", "")
    return cat if cat in vault["categories"] else ""


def search_entries(vault: dict, query: str,
                   category: str | None = None) -> list[tuple[str, dict]]:
    """Entries matching *query*, sorted by name (search.md).

    The query is split into words, and an entry matches when EVERY word appears somewhere in
    it -- not necessarily in the same field, and in any order -- so "gmail work" finds an entry
    named "Work Gmail" (ROLO-0009). A one-word query behaves exactly as the old substring
    search did. *category* narrows the result to one category ("" = uncategorised); None
    applies no category filter.
    """
    words = query.lower().split()
    results = []
    for eid, entry in vault["entries"].items():
        if category is not None and entry_category(vault, entry) != category:
            continue
        texts = _entry_search_texts(entry)
        if all(any(w in t for t in texts) for w in words):
            results.append((eid, entry))
    return sorted(results, key=lambda x: x[1]["name"].lower())


def list_entries(vault: dict) -> list[tuple[str, dict]]:
    return sorted(vault["entries"].items(), key=lambda x: x[1]["name"].lower())


def name_key(name: str) -> str:
    """The one definition of "same entry name": case-insensitive, whitespace-trimmed.

    The editor's duplicate warning and the import dedup used to spell this separately, and
    one of them forgot the strip (ROLO-0065).
    """
    return name.strip().lower()


def find_entry_by_name(vault: dict, name: str, exclude_id: str | None = None) -> str | None:
    """Return the id of an existing entry whose name matches `name` under name_key(), or
    None. `exclude_id` skips one entry so editing an entry doesn't flag itself as its own
    duplicate. Used to warn on duplicate names (ROLO-0023)."""
    target = name_key(name)
    for eid, entry in vault["entries"].items():
        if eid == exclude_id:
            continue
        if name_key(entry["name"]) == target:
            return eid
    return None


def move_item(items: list, item: Any, target: Any) -> list:
    """Return *items* with *item* moved into *target*'s slot, for drag and keyboard reordering.

    Moving DOWN lands after the target and moving up lands before it, so a move onto the
    next row down swaps the two. Inserting before the target in both directions made a
    one-row move down a no-op -- the bug categories once had, and fields still did until
    Ctrl+Down depended on it (ROLO-0053).
    """
    items = list(items)
    if item not in items or target not in items or item is target:
        return items
    old_idx, target_idx = items.index(item), items.index(target)
    items.pop(old_idx)
    new_idx = items.index(target) + (1 if old_idx < target_idx else 0)
    items.insert(new_idx, item)
    return items


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
    # The whole file is read and re.split in memory, so a mis-picked multi-gigabyte file was an
    # out-of-memory kill rather than a message (ROLO-0050). Checked on the size the open file
    # reports, and enforced again on the read, so a file growing mid-read cannot slip past.
    with open(filepath, "r", encoding="utf-8") as fp:
        if os.fstat(fp.fileno()).st_size > MAX_IMPORT_BYTES:
            raise ValueError(_IMPORT_TOO_LARGE)
        content = fp.read(MAX_IMPORT_BYTES + 1)
        if len(content) > MAX_IMPORT_BYTES:
            raise ValueError(_IMPORT_TOO_LARGE)
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


def duplicate_flags(vault: dict, parsed: list[dict]) -> list[bool]:
    """For each parsed entry, whether it duplicates a vault entry OR an earlier one in the file.

    The import preview draws from this, so what it marks as a duplicate is exactly what
    import_entries(skip_duplicates=True) would skip (ROLO-0047). The preview used to check the
    vault only, so two same-named entries in one file both rendered unmarked.
    """
    seen = {name_key(e["name"]) for e in vault["entries"].values()}
    flags = []
    for entry_data in parsed:
        key = name_key(entry_data["name"])
        flags.append(key in seen)
        seen.add(key)
    return flags


def import_entries(vault: dict, parsed: list[dict], skip_duplicates: bool = True,
                   category: str = "") -> tuple[int, int]:
    """Add parsed entries to the vault, returning (imported, skipped).

    skip_duplicates=False imports every entry given. The import preview passes that, because
    it hands over only what the user ticked -- a duplicate they ticked deliberately must land,
    not vanish behind a checkbox that did nothing (ROLO-0047). `category` files every imported
    entry under one existing category; "" leaves them uncategorised (ROLO-0067).
    """
    if category and category not in vault["categories"]:
        raise ValueError(f"No category named {category!r}")
    flags = duplicate_flags(vault, parsed) if skip_duplicates else [False] * len(parsed)
    imported = skipped = 0
    for entry_data, is_dup in zip(parsed, flags):
        if is_dup:
            skipped += 1
            continue
        add_entry(vault, entry_data["name"], entry_data["fields"], entry_data["notes"], category)
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
    at least once when the length allows it.

    The guarantee is met by rejection sampling: draw every character uniformly from the
    combined pool and redraw the whole password while a class is missing. Seeding one character
    per class and filling the rest over-represented the smaller classes (ROLO-0069). Rejection
    keeps each accepted password uniform over the passwords that satisfy the guarantee.
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
    # With fewer characters than classes the guarantee cannot hold, so require only as many
    # distinct classes as there are characters -- any of them, never a fixed subset.
    need = min(len(pools), length)
    while True:
        candidate = "".join(secrets.choice(combined) for _ in range(length))
        if sum(any(c in pool for c in candidate) for pool in pools) >= need:
            return candidate


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
                proc = subprocess.run(cmd, capture_output=True, timeout=5, check=False)
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
                proc = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True,
                                      timeout=5, check=False)
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


def clear_clipboard_if_unchanged(value: str) -> None:
    """Wipe the clipboard, but only if it still holds *value* (ROLO-0003).

    If a reader is available and the clipboard has moved on, the user's new copy is left
    alone. wl-copy --trim-newline stores a trailing-newline value trimmed, so the trimmed form
    counts as unchanged too -- otherwise such a value never matches and the secret stays on
    the clipboard for good. No reader available falls through to the wipe deliberately: for a
    credential manager, clearing a clipboard we cannot inspect is the safe direction.
    Runs helper processes, so the GUI calls it off the main thread (ROLO-0046).
    """
    current = read_clipboard()
    if current is not None and current != value and current != value.rstrip("\n"):
        return
    copy_to_clipboard("")


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


def save_config(data: dict, path: str | None = None) -> bool:
    """Merge *data* into .rolodex.conf atomically. Returns False when the write failed.

    Failure is still swallowed rather than raised -- nothing here is worth interrupting the user
    over -- but it is REPORTED, so the one caller whose preference matters can say so
    (ROLO-0063): the update opt-in toasted success even when a read-only directory or a full
    disk had dropped it.
    """
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
        # the user; the return value lets a caller say so where it matters.
        return False
    return True


def config_choice(conf: dict, key: str, allowed: Collection[str], default: str) -> str:
    """A string setting from the hand-editable config, or *default* when it is not one of
    *allowed* -- the same fall-back-rather-than-raise contract as config_int."""
    value = conf.get(key, default)
    return value if isinstance(value, str) and value in allowed else default


# ---------------------------------------------------------------------------
# Themes and accent colours (ROLO-0015)
#
# A theme is a palette of named colours; CUSTOM_CSS refers only to those names (@rolo_*), so
# swapping the palette restyles everything. The field-category colours in each palette were
# chosen so every pair stays distinguishable under protanopia, deuteranopia and tritanopia
# simulations -- tests/test_themes.py checks that, and the text contrast, for every palette.
# ---------------------------------------------------------------------------

THEME_KEY = "theme"
ACCENT_KEY = "accent"
THEMES = {"auto": "Automatic", "dark": "Dark", "light": "Light",
          "high-contrast": "High contrast"}
DEFAULT_THEME = "auto"
# libadwaita's own accent values, so a preset matches what the desktop would draw. Red and
# yellow are left out on purpose: red reads as "delete" here, and yellow text is unreadable on
# the Light theme (user decision 2026-09-25).
ACCENT_PRESETS = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a",
                  "orange": "#ed5b00", "pink": "#d56199", "purple": "#9141ac",
                  "slate": "#6f8396"}
ACCENT_SYSTEM = "system"
DEFAULT_ACCENT = ACCENT_SYSTEM
FALLBACK_ACCENT = ACCENT_PRESETS["blue"]

PALETTES: dict[str, dict] = {
    # Today's glass look.
    "dark": {
        "dark": True,
        "glow": True,
        "surfaces": ("#0d1117", "#161b22", "#1b2027"),
        "colors": {
            "bg_1": "#0d1117", "bg_2": "#161b22", "bg_3": "#0f1923", "bg_dialog": "#131a24",
            "sidebar_1": "rgba(13,17,23,0.95)", "sidebar_2": "rgba(22,27,34,0.9)",
            "card_bg": "rgba(255,255,255,0.04)", "card_bg_strong": "rgba(255,255,255,0.06)",
            "card_border": "rgba(255,255,255,0.08)", "row_line": "rgba(255,255,255,0.04)",
            "hover": "rgba(255,255,255,0.04)", "hover_soft": "rgba(255,255,255,0.02)",
            "highlight": "rgba(255,255,255,0.05)", "shadow": "rgba(0,0,0,0.3)",
            "shadow_soft": "rgba(0,0,0,0.2)", "headerbar_bg": "rgba(13,17,23,0.85)",
            "headerbar_border": "rgba(255,255,255,0.06)", "notes": "#2aa1b3",
            "notes_bg": "rgba(255,255,255,0.03)", "pill_bg": "rgba(255,255,255,0.05)",
            "pill_border": "rgba(255,255,255,0.1)", "count_bg": "rgba(255,255,255,0.06)",
            "destructive_glow": "rgba(224,27,36,0.25)",
            "destructive_glow_strong": "rgba(224,27,36,0.35)",
            "reveal_hover_bg": "rgba(245,194,17,0.1)",
            "reveal_hover_border": "rgba(245,194,17,0.25)",
        },
        "text": {"dim": "#8b949e", "muted": "#8b949e", "masked": "#848d97",
                 "revealed": "#f5c211"},
        "fields": {"credential": "#f6d32d", "key": "#dc8add", "identity": "#1c71d8",
                   "url": "#57e389", "date": "#e66100", "other": "#77767b"},
        "extra_css": "",
    },
    "light": {
        "dark": False,
        "glow": False,
        "surfaces": ("#ffffff", "#eef2f7", "#f7f9fc"),
        "colors": {
            "bg_1": "#f7f9fc", "bg_2": "#eef2f7", "bg_3": "#f3f6fa", "bg_dialog": "#f1f4f8",
            "sidebar_1": "rgba(255,255,255,0.75)", "sidebar_2": "rgba(240,244,249,0.85)",
            "card_bg": "rgba(255,255,255,0.7)", "card_bg_strong": "rgba(255,255,255,0.85)",
            "card_border": "rgba(20,30,50,0.1)", "row_line": "rgba(20,30,50,0.06)",
            "hover": "rgba(20,30,50,0.05)", "hover_soft": "rgba(20,30,50,0.03)",
            "highlight": "rgba(255,255,255,0.8)", "shadow": "rgba(30,45,70,0.12)",
            "shadow_soft": "rgba(30,45,70,0.08)", "headerbar_bg": "rgba(247,249,252,0.9)",
            "headerbar_border": "rgba(20,30,50,0.08)", "notes": "#1b7a8a",
            "notes_bg": "rgba(255,255,255,0.6)", "pill_bg": "rgba(255,255,255,0.8)",
            "pill_border": "rgba(20,30,50,0.12)", "count_bg": "rgba(20,30,50,0.07)",
            "destructive_glow": "rgba(192,28,40,0.18)",
            "destructive_glow_strong": "rgba(192,28,40,0.28)",
            "reveal_hover_bg": "rgba(156,110,3,0.08)",
            "reveal_hover_border": "rgba(156,110,3,0.25)",
        },
        "text": {"dim": "#57606a", "muted": "#57606a", "masked": "#636c76",
                 "revealed": "#845400"},
        "fields": {"credential": "#a77605", "key": "#813d9c", "identity": "#3387f8",
                   "url": "#1b7f4d", "date": "#b10025", "other": "#6e6e78"},
        "extra_css": "",
    },
    # One look, white on black, whatever the desktop does (user decision 2026-09-25).
    "high-contrast": {
        "dark": True,
        "glow": False,
        "surfaces": ("#000000",),
        "colors": {
            "bg_1": "#000000", "bg_2": "#000000", "bg_3": "#000000", "bg_dialog": "#000000",
            "sidebar_1": "#000000", "sidebar_2": "#000000",
            "card_bg": "#000000", "card_bg_strong": "#000000",
            "card_border": "#ffffff", "row_line": "rgba(255,255,255,0.5)",
            "hover": "rgba(255,255,255,0.18)", "hover_soft": "rgba(255,255,255,0.12)",
            "highlight": "transparent", "shadow": "transparent", "shadow_soft": "transparent",
            "headerbar_bg": "#000000", "headerbar_border": "#ffffff", "notes": "#33e0ff",
            "notes_bg": "#000000", "pill_bg": "#000000", "pill_border": "#ffffff",
            "count_bg": "rgba(255,255,255,0.2)", "destructive_glow": "transparent",
            "destructive_glow_strong": "transparent",
            "reveal_hover_bg": "rgba(242,208,1,0.2)", "reveal_hover_border": "#f2d001",
        },
        "text": {"dim": "#d0d0d0", "muted": "#d0d0d0", "masked": "#c8c8c8",
                 "revealed": "#f2d001"},
        "fields": {"credential": "#f2d001", "key": "#d873b1", "identity": "#5694fe",
                   "url": "#31ff9a", "date": "#fa693d", "other": "#fefefe"},
        "extra_css": """
.boxed-list, .notes-frame, .reveal-btn, .edit-btn { border-width: 2px; }
.field-credential, .field-key, .field-identity, .field-url, .field-date, .field-other,
.notes-frame { border-left-width: 5px; }
.navigation-sidebar row:selected { background: @rolo_accent_bg; color: #ffffff; }
.notes-frame { border-color: @rolo_notes; }
""",
        # libadwaita's own surfaces, so its widgets are black and white too.
        "adw": {"window_bg_color": "#000000", "window_fg_color": "#ffffff",
                "view_bg_color": "#000000", "view_fg_color": "#ffffff",
                "headerbar_bg_color": "#000000", "headerbar_fg_color": "#ffffff",
                "card_bg_color": "#000000", "card_fg_color": "#ffffff",
                "dialog_bg_color": "#000000", "dialog_fg_color": "#ffffff",
                "popover_bg_color": "#000000", "popover_fg_color": "#ffffff",
                "sidebar_bg_color": "#000000", "sidebar_fg_color": "#ffffff"},
    },
}


def _hex_rgb(color: str) -> tuple[float, float, float]:
    h = color.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


def _rgb_hex(rgb: Iterable[float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def mix_hex(a: str, b: str, t: float) -> str:
    """*a* moved fraction *t* of the way to *b*, in sRGB."""
    ra, rb = _hex_rgb(a), _hex_rgb(b)
    return _rgb_hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a #rrggbb colour."""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _hex_rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two #rrggbb colours (1.0 to 21.0)."""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_against(color: str, surfaces: Iterable[str], toward: str,
                     target: float = 4.5) -> str:
    """*color*, mixed toward *toward* in small steps until it reaches *target* contrast
    against every one of *surfaces*. An accent is a hue first; this keeps its hue while
    making text drawn in it readable, whatever accent the desktop hands over."""
    for step in range(21):
        candidate = mix_hex(color, toward, step / 20)
        if all(contrast_ratio(candidate, s) >= target for s in surfaces):
            return candidate
    return toward


def resolve_palette(theme: str, system_dark: bool) -> str:
    """The palette a theme setting draws with. Only Automatic depends on the desktop."""
    if theme == "auto":
        return "dark" if system_dark else "light"
    return theme if theme in PALETTES else "dark"


def accent_tokens(accent: str, palette: dict) -> dict[str, str]:
    """The colours derived from one accent for one palette.

    accent_bg carries white text at 4.5:1 (buttons, selection); accent_text is readable on
    the palette's surfaces (titles, links, the live code)."""
    accent_bg = readable_against(accent, ("#ffffff",), "#000000")
    toward = "#ffffff" if palette["dark"] else "#000000"
    return {
        "accent": accent,
        "accent_bg": accent_bg,
        "accent_bg_dark": mix_hex(accent_bg, "#000000", 0.2),
        "accent_text": readable_against(accent, palette["surfaces"], toward),
        "glow": accent if palette["glow"] else "transparent",
    }


def theme_css(palette_name: str, accent: str, css_vars: bool = True) -> str:
    """The whole stylesheet for one palette and accent: its @define-color block, CUSTOM_CSS,
    then the palette's own extra rules -- last, so they win over CUSTOM_CSS at equal weight.

    *css_vars* also sets libadwaita's CSS custom properties, which it reads from 1.6 on; GTK
    before 4.16 cannot parse them, so the caller turns them off there.
    """
    palette = PALETTES[palette_name]
    tokens = dict(palette["colors"])
    tokens.update(palette["text"])
    tokens.update({f"field_{k}": v for k, v in palette["fields"].items()})
    derived = accent_tokens(accent, palette)
    tokens.update(derived)
    lines = [f"@define-color rolo_{name} {value};" for name, value in tokens.items()]
    adw = {"accent_bg_color": derived["accent_bg"], "accent_fg_color": "#ffffff",
           "accent_color": derived["accent_text"]}
    adw.update(palette.get("adw", {}))
    lines += [f"@define-color {name} {value};" for name, value in adw.items()]
    if css_vars:
        props = "".join(f"  --{name.replace('_color', '').replace('_', '-')}-color: {value};\n"
                        for name, value in adw.items())
        lines.append(":root {\n" + props + "}")
    return "\n".join(lines) + "\n" + CUSTOM_CSS + palette["extra_css"]


# ---------------------------------------------------------------------------
# Opt-in signed auto-update (ROLO-0037)
# Contract: docs/specs/ROLO-0037-auto-update.md
#
# OFF by default (INV-1) and the app's only network egress (INV-3). Nothing here reads the
# vault or the master password (INV-4). urllib.request is imported INSIDE the fetch helpers
# and never at module scope, so `import rolodex` does not load it (INV-12) -- note the
# module-scope `urllib.parse` near the top is for TOTP otpauth:// parsing and is expected.
# ---------------------------------------------------------------------------

# The release-signing public key, base64 of 32 raw Ed25519 bytes (D3/D4). Real since
# ROLO-0041 (2026-09-21); it superseded a 32-zero placeholder that made the feature fail
# closed -- able to offer an update and never install one.
#
# The private half exists only as the GitHub Actions secret ROLODEX_SIGNING_KEY and in the
# maintainer's own backup, never in this repository. Losing it is unrecoverable: every binary
# already shipped trusts this key and nothing else, so it would refuse every update that
# followed. Changing it has the same effect on installed builds, so it is not a routine
# rotation -- generate a pair only with scripts/gen-signing-key.py.
#
# INV-11 asserts this is not the placeholder, so a revert or a bad merge cannot quietly put
# the feature back to failing closed.
RELEASE_PUBLIC_KEY_B64 = "h+yKUqu8jdoVicBBMHbIjcZxRmjsW2VCg+QgAQw7khM="

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
# ROLO-0058: wall-clock budget for one download. UPDATE_TIMEOUT_S bounds each socket
# operation only; this bounds the transfer. 15 minutes covers the asset cap at ~300 KB/s.
UPDATE_DOWNLOAD_BUDGET_S = 15 * 60
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


def parse_version(text: str) -> tuple[int, ...] | None:
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


def version_gt(latest: tuple[int, ...], current: tuple[int, ...]) -> bool:
    """True iff *latest* is strictly greater, zero-padding the shorter tuple so that
    (0, 1) and (0, 1, 0) compare EQUAL rather than one being newer (D10)."""
    width = max(len(latest), len(current))
    return latest + (0,) * (width - len(latest)) > current + (0,) * (width - len(current))


def version_string(tag: str) -> str:
    """A tag's bare version -- one leading v/V stripped. This is the form stored as the
    skipped version and shown to the user."""
    return tag[1:] if tag[:1] in ("v", "V") else tag


def platform_asset_name() -> str | None:
    """This build's release asset name, or None where self-update is unsupported (D2)."""
    import platform

    return PLATFORM_ASSETS.get((sys.platform, platform.machine()))


def detect_installer() -> str | None:
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


def set_update_check_enabled(enabled: bool, path: str | None = None) -> bool:
    """Persist the opt-in. Returns False when it could not be written (ROLO-0063)."""
    return save_config({UPDATE_ENABLED_KEY: bool(enabled)}, path)


def update_skipped_version(path: str | None = None) -> str:
    value = load_config(path).get(UPDATE_SKIPPED_KEY)
    return value if isinstance(value, str) else ""


def skip_update_version(version: str, path: str | None = None) -> None:
    """Persist a skipped version (INV-7). save_config swallows OSError by design, so a skip
    that cannot be written is dropped silently and the version is offered again next launch.
    That is accepted rather than made fatal -- see the spec's INV-7."""
    save_config({UPDATE_SKIPPED_KEY: version}, path)


def select_update_assets(assets: list[dict], asset_name: str) -> tuple[str, str] | None:
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


def release_public_key() -> "Ed25519PublicKey":
    """The built-in release-signing public key (INV-8/INV-11)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    return Ed25519PublicKey.from_public_bytes(base64.b64decode(RELEASE_PUBLIC_KEY_B64))


def _require_https(url: str) -> None:
    """Refuse any non-https URL (INV-9). Defence in depth: even a tampered API response
    pointing an asset at http:// or file:// is never opened."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise UpdateError("refusing a non-https update URL")


# ROLO-0058: the only hosts the updater talks to. A release's download links point at
# github.com and redirect to a *.githubusercontent.com asset host; the API is api.github.com.
_UPDATE_HOST_SUFFIXES = (".github.com", ".githubusercontent.com")


def _require_update_url(url: str) -> None:
    """Refuse any URL that is not https to a GitHub host (INV-9, ROLO-0058).

    asset_url and sig_url come straight from the release JSON, so a tampered or
    attacker-authored release record could point a 250 MB fetch at any TLS host and disclose the
    user's IP outside GitHub -- weaker than INV-3's "the app's only network egress". The
    Ed25519 check still guards the installed bytes; this keeps the connection itself on GitHub.
    Applied to the first URL and to every redirect hop.
    """
    _require_https(url)
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host != "github.com" and not host.endswith(_UPDATE_HOST_SUFFIXES):
        raise UpdateError(f"refusing an update URL outside GitHub ({host or 'no host'})")


def _read_capped(response: Any, max_bytes: int, deadline: float,
                 sink: IO[bytes] | None = None) -> bytes:
    """Read *response* in chunks, enforcing a byte cap AND a wall-clock deadline (INV-9).

    urlopen's timeout bounds each socket operation, not the transfer, so a server sending one
    byte every 29 s held the connection open indefinitely (ROLO-0058). The deadline is checked
    between chunks, so the worst case is the budget plus one socket timeout. With *sink* the
    chunks are written there and b"" is returned; without, they are returned joined.
    """
    received = 0
    parts = []
    while True:
        if time.monotonic() > deadline:
            raise UpdateError("the update server is too slow; try again later")
        chunk = response.read(_DOWNLOAD_CHUNK)
        if not chunk:
            break
        received += len(chunk)
        if received > max_bytes:
            raise UpdateError("download exceeds the size cap")
        if sink is None:
            parts.append(chunk)
        else:
            sink.write(chunk)
    return b"".join(parts)


_TLS_CONTEXT: "ssl.SSLContext | None" = None


def _tls_context() -> "ssl.SSLContext":
    """The updater's SSLContext, built once per process (ROLO-0080).

    CA trust is certifi when it is importable, else the system store (D7). The frozen binaries
    are built on one distro and run on any, and a host whose CA bundle sits somewhere the
    frozen OpenSSL does not look yields no CAs at all -- which INV-13 would then swallow as a
    silent "no update". A source checkout has no such problem and needs no extra dependency.
    """
    global _TLS_CONTEXT
    if _TLS_CONTEXT is None:
        import ssl

        try:
            import certifi

            _TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _TLS_CONTEXT = ssl.create_default_context()
    return _TLS_CONTEXT


def _opener() -> "urllib.request.OpenerDirector":
    """A urllib opener that can speak https ONLY and re-checks every redirect hop (INV-9).

    Built from an empty OpenerDirector rather than build_opener, which always registers the
    plain HTTPHandler: then http:// stayed reachable at the urllib layer and only a check kept
    it out (ROLO-0080). Here there is no handler for any other scheme, so a non-https URL fails
    with "unknown url type" even if a future edit dropped the check. The redirect handler still
    refuses a hop off https or off GitHub before following it, because urllib's default would
    transparently follow a 3xx to http://.
    """
    import urllib.request

    class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: "urllib.request.Request", fp: Any, code: int,
                             msg: str, headers: Any,
                             newurl: str) -> "urllib.request.Request | None":
            _require_update_url(newurl)  # raises before the redirect is followed
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(context=_tls_context()),
        _HttpsOnlyRedirect(),
        urllib.request.UnknownHandler(),  # any other scheme -> URLError("unknown url type")
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def fetch_latest_release(owner: str = GITHUB_OWNER, repo: str = GITHUB_REPO) -> dict:
    """GET /repos/{owner}/{repo}/releases/latest and return the parsed JSON.

    /releases/latest excludes prereleases, so an rc tag is never offered. The request carries a
    fixed User-Agent and nothing else -- no query string, no cookie, no identifier (INV-3).
    """
    import urllib.request

    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    _require_update_url(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "rolodex-updater", "Accept": "application/vnd.github+json"},
    )
    deadline = time.monotonic() + UPDATE_TIMEOUT_S
    with _opener().open(request, timeout=UPDATE_TIMEOUT_S) as response:
        raw = _read_capped(response, MAX_API_BYTES, deadline)
    return json.loads(raw.decode("utf-8"))


def download_to(url: str, dest: str, max_bytes: int) -> None:
    """Stream *url* to *dest*, aborting once the running total exceeds *max_bytes* (INV-9).
    Any failure deletes the partial file, so a broken download never leaves bytes behind."""
    import urllib.request

    _require_update_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "rolodex-updater"})
    deadline = time.monotonic() + UPDATE_DOWNLOAD_BUDGET_S
    try:
        with _opener().open(request, timeout=UPDATE_TIMEOUT_S) as response, open(dest, "wb") as fh:
            _read_capped(response, max_bytes, deadline, sink=fh)
    except BaseException:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise


class UpdateInfo:
    """A newer, signed, non-skipped release the user may install (INV-5)."""

    def __init__(self, version: str, asset_url: str, sig_url: str, notes: str) -> None:
        self.version = version
        self.asset_url = asset_url
        self.sig_url = sig_url
        self.notes = notes


def check_for_update(*, force: bool = False, fetcher: Callable[[], dict] | None = None,
                     current_version: str | None = None,
                     config_path: str | None = None) -> "UpdateInfo | None":
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
        asset_name = platform_asset_name()
        if asset_name is None:
            return None
        urls = select_update_assets(release.get("assets") or [], asset_name)
        if urls is None:
            return None
        return UpdateInfo(version, urls[0], urls[1], release.get("body") or "")
    except Exception as exc:
        if force:
            raise UpdateError(f"could not check for updates: {exc}") from exc
        return None


def download_and_verify(info: UpdateInfo, *,
                        downloader: Callable[[str, str, int], None] | None = None,
                        target: str | None = None) -> str:
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


def apply_update(new_file: str, *, target: str | None = None,
                 on_before_exec: Callable[[], None] | None = None) -> NoReturn:
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


def sweep_stale_update_temps(target: str | None = None) -> int:
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


def a11y_label(widget, text: str):
    """Give a widget with no visible name one a screen reader can speak (ROLO-0053). A tooltip
    is not one: it is exposed as a description, so the button itself was announced as nameless.
    Nor is an entry's placeholder, which is gone once the box holds text (ROLO-0017)."""
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    return widget


def attach_reorder_keys(row, reorder, refocus) -> None:
    """Ctrl+Up / Ctrl+Down on *row* call reorder(row, neighbour), mirroring drag-and-drop.

    *refocus* runs once the move has settled and puts the focus back on the moved item: both
    reorders rebuild their list, which drops the focus.

    The drag handle is an image, which cannot take focus, so without this a keyboard-only user
    could not reorder fields or categories at all (ROLO-0053). Capture phase, because the row's
    entries would otherwise see the keys first.
    """
    def on_key(_ctrl, keyval, _code, state):
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        if keyval == Gdk.KEY_Up:
            neighbour = row.get_prev_sibling()
        elif keyval == Gdk.KEY_Down:
            neighbour = row.get_next_sibling()
        else:
            return False
        if neighbour is not None:
            reorder(row, neighbour)
            GLib.idle_add(lambda: refocus() and False)
        return True

    ctrl = Gtk.EventControllerKey()
    ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    ctrl.connect("key-pressed", on_key)
    row.add_controller(ctrl)


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

    def __init__(self, app, vault_path, is_new, notice=""):
        super().__init__(title="Rolodex", application=app)
        self.app = app
        self.vault_path = vault_path
        self.is_new = is_new
        # ROLO-0044: taken when the user commits to unlocking or creating, handed to MainWindow
        # on success, released on every failure path.
        self.lock = VaultLock(vault_path)
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

        if notice:
            note = Gtk.Label(label=notice, xalign=0)
            note.set_wrap(True)
            vbox.append(note)

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

        # ROLO-0045: routes out of a vault that will not open. Hidden until a load fails for a
        # reason other than the password -- a wrong password must never offer to replace the
        # vault. In create mode the restore button doubles as "use an existing vault", which is
        # how a user moving from a source run to the packaged build finds their vault
        # (ROLO-0078).
        self.recover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        restore_btn = Gtk.Button(
            label="Use an Existing Vault File…" if is_new else "Restore from Backup…")
        restore_btn.add_css_class("flat")
        restore_btn.connect("clicked", self._on_restore_backup)
        self.recover_box.append(restore_btn)
        if not is_new:
            new_btn = Gtk.Button(label="Start a New Vault…")
            new_btn.add_css_class("flat")
            new_btn.connect("clicked", self._on_start_new)
            self.recover_box.append(new_btn)
        self.recover_box.set_visible(is_new)
        vbox.append(self.recover_box)

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

    def _take_lock(self) -> bool:
        """Take the vault lock (ROLO-0044). False, with the reason shown, if another copy of
        Rolodex has this vault open. A lock file that cannot be created at all -- a read-only
        directory -- does not block unlocking: the lock guards writes, and the change check in
        MainWindow._write_vault still runs."""
        try:
            self.lock.acquire()
        except VaultBusyError as exc:
            self._show_error(str(exc))
            return False
        except OSError:
            pass
        return True

    def _on_activate(self, *_args):
        pw = self.pw_entry.get_text()
        if not pw:
            self._show_error("Please enter a password.")
            return
        if not self.btn.get_sensitive():
            return  # an unlock is already running

        if self.is_new:
            if len(pw) < MIN_PASSWORD_LENGTH:
                self._show_error(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
                return
            pw2 = self.pw_confirm.get_text()
            if pw != pw2:
                self._show_error("Passwords do not match.")
                return
            if not self._take_lock():
                return
            # The KDF runs off the main thread here too, as it does for unlock (INV-6). On the
            # main thread the window froze for the whole derivation with the button still live
            # and nothing saying why (ROLO-0070).
            self.btn.set_sensitive(False)
            self.btn.set_label("Creating...")
            import threading
            threading.Thread(target=self._try_create, args=(pw,), daemon=True).start()
        else:
            if not self._take_lock():
                return
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
        except ValueError as e:
            # Bad magic, a truncated salt, or ciphertext that decrypted to something that is not
            # a vault: the FILE is the problem, not the password (ROLO-0045).
            GLib.idle_add(self._unlock_fail, str(e), True)
        except Exception as e:  # noqa: BLE001 - a thread's escape would strand "Unlocking..."
            GLib.idle_add(self._unlock_fail, str(e))

    def _unlock_ok(self, vault, salt, pw, key):
        # Everything after a SUCCESSFUL decrypt needs its own handler. This runs from a
        # GLib.idle_add callback with the unlock button already disabled, so an exception here
        # (a malformed vault reaching migrate_vault, a bad value in .rolodex.conf reaching
        # MainWindow.__init__) froze the dialog on "Unlocking..." forever, with the vault
        # decrypted in memory and nothing on screen explaining why.
        try:
            migrate_vault(vault)
            self.app.open_main(vault, salt, pw, self.vault_path, key, lock=self.lock)
        except Exception as exc:  # noqa: BLE001 - last resort; the alternative is a frozen dialog
            self._unlock_fail(f"The vault opened but could not be loaded: {exc}")
            return
        self._wipe_password_entries()
        self.close()

    def _try_create(self, pw):
        try:
            vault, salt, key = create_vault_with_key(pw, self.vault_path)
            GLib.idle_add(self._create_ok, vault, salt, pw, key)
        except Exception as e:  # noqa: BLE001 - a thread's escape would strand "Creating..."
            GLib.idle_add(self._create_fail, str(e))

    def _create_ok(self, vault, salt, pw, key):
        self.app.open_main(vault, salt, pw, self.vault_path, key, lock=self.lock)
        self._wipe_password_entries()
        self.close()
        return False

    def _create_fail(self, msg):
        self.lock.release()
        self.btn.set_sensitive(True)
        self.btn.set_label("Create Vault")
        self._show_error(msg)
        return False

    def _wipe_password_entries(self):
        """Drop the master password from the entry buffers once it has been handed over
        (ROLO-0059). Success paths only: INV-7 refocuses this field after a wrong password,
        so clearing it there would hand the user an empty box to correct. This cannot unmake
        the Python str already read out of the buffer -- see the roadmap item."""
        self.pw_entry.set_text("")
        if self.is_new:
            self.pw_confirm.set_text("")

    def _unlock_fail(self, msg, unreadable=False):
        self.lock.release()
        self.btn.set_sensitive(True)
        self.btn.set_label("Unlock")
        self._show_error(msg)
        if unreadable:
            self.recover_box.set_visible(True)
        self.pw_entry.grab_focus()
        return False

    # --- ROLO-0045: getting out of a vault that will not open ---------------------------

    def _reopen(self, is_new, notice):
        """Replace this screen with a fresh one in the given mode."""
        self.lock.release()
        UnlockDialog(self.app, self.vault_path, is_new, notice).present()
        self.close()

    def _on_restore_backup(self, *_args):
        chooser = Gtk.FileDialog()
        chooser.set_title("Choose a Rolodex vault or backup")
        vault_filter = Gtk.FileFilter()
        vault_filter.set_name("Rolodex vaults")
        vault_filter.add_pattern("*.vault")
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(vault_filter)
        filters.append(all_filter)
        chooser.set_filters(filters)
        backups = os.path.join(os.path.dirname(os.path.abspath(self.vault_path)), "Backups")
        start = backups if os.path.isdir(backups) else GLib.get_home_dir()
        if start:
            chooser.set_initial_folder(Gio.File.new_for_path(start))
        chooser.open(self, None, self._on_restore_backup_chosen)

    def _on_restore_backup_chosen(self, chooser, result):
        try:
            gfile = chooser.open_finish(result)
        except GLib.Error:
            return
        source = gfile.get_path()
        if not source:
            return
        self._adopt(source)

    def _adopt(self, source):
        """Install *source* as the vault, then switch to unlocking it."""
        if not self._take_lock():
            return
        try:
            aside = adopt_vault_file(source, self.vault_path)
        except (OSError, ValueError) as exc:
            self.lock.release()
            self._show_error(str(exc))
            return
        notice = "Vault file installed. Enter its master password to unlock it."
        if aside:
            notice += f" The unreadable vault was kept as {os.path.basename(aside)}."
        self._reopen(False, notice)

    def _on_start_new(self, *_args):
        dialog = Adw.AlertDialog(
            heading="Start a New Vault?",
            body=(
                "The vault that will not open is kept, renamed beside the original with "
                "“.unreadable-” and the date added, so it can still be recovered. A new, "
                "empty vault is then created with a new master password."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("new", "Start New Vault")
        dialog.set_response_appearance("new", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_start_new_response)
        dialog.present(self)

    def _on_start_new_response(self, _dialog, response):
        if response != "new":
            return
        if not self._take_lock():
            return
        try:
            aside = set_aside_vault(self.vault_path)
        except OSError as exc:
            self.lock.release()
            self._show_error(f"Could not move the unreadable vault aside: {exc}")
            return
        notice = f"The unreadable vault was kept as {os.path.basename(aside)}." if aside else ""
        self._reopen(True, notice)


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

        # Disclosure arrow. A picture of the row's EXPANDED state, so a screen reader is told
        # the state itself and skips the picture (ROLO-0017).
        arrow_icon = "pan-end-symbolic" if collapsed else "pan-down-symbolic"
        self.arrow = Gtk.Image(icon_name=arrow_icon,
                               accessible_role=Gtk.AccessibleRole.PRESENTATION)
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
        a11y_label(self, f"{display_name}, {count} {entries_noun(count)}")
        # An int, not a bool: GTK reads this state as an int, and a Python bool arrives as a
        # GValue it cannot read, so the state is dropped with only a console warning.
        self.update_state([Gtk.AccessibleState.EXPANDED], [int(not collapsed)])

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
        # Deferred: the move rebuilds the sidebar, which destroys THIS row -- and the DropTarget
        # whose ::drop handler is still executing. Letting the drop finish first keeps GTK from
        # running a handler on a finalised widget (ROLO-0070).
        GLib.idle_add(widget._move_entry_to_category_idle, dragged_row.entry_id,
                      self.category_name)
        return True


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, vault, salt, password, vault_path, key=None, lock=None):
        super().__init__(application=app, title="Rolodex")
        self.app_ref = app
        # ROLO-0044: the lock the unlock screen took, held until this window closes, and what
        # this session last saw of the vault file. _write_vault compares against the
        # fingerprint before every write, so a vault something else rewrote is never
        # silently overwritten.
        self._vault_lock = lock
        self._disk_fingerprint = vault_fingerprint(vault_path)
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
        # ROLO-0068: whether the system clock is synchronised, checked once per unlock off the
        # main thread. None until known, and None where it cannot be known -- only an explicit
        # False puts a warning on the code rows.
        self._clock_synced = None
        import threading

        threading.Thread(target=self._check_clock, daemon=True).start()

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
        add_btn = a11y_label(
            Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add entry (Ctrl+N)"), "Add entry")
        add_btn.connect("clicked", self._on_add)
        header.pack_start(add_btn)

        # Right side: menu
        menu = Gio.Menu()
        menu.append("Preferences...", "win.preferences")
        menu.append("Password health...", "win.health")
        menu.append("Manage categories...", "win.manage-categories")
        menu.append("Import from text file...", "win.import")
        menu.append("Backup vault...", "win.backup")
        menu.append("Restore vault from backup...", "win.restore")
        menu.append("Export (decrypted plaintext)...", "win.export")
        menu.append("Change master password...", "win.chpass")
        menu.append("Check for updates...", "win.check-updates")
        menu.append("Check for updates automatically", "win.auto-updates")
        menu_btn = a11y_label(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu),
                              "Main menu")
        header.pack_end(menu_btn)

        # Manual Lock button (ROLO-0002), also on Ctrl+L.
        lock_btn = a11y_label(Gtk.Button(icon_name="changes-prevent-symbolic",
                                         tooltip_text="Lock vault (Ctrl+L)"), "Lock vault")
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
            ("preferences", self._on_preferences, ["<Control>comma"]),
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
        self.search_entry = a11y_label(Gtk.SearchEntry(placeholder_text="Search entries..."),
                                       "Search entries")
        self.search_entry.set_margin_top(8)
        self.search_entry.set_margin_start(8)
        self.search_entry.set_margin_end(8)
        self.search_entry.set_margin_bottom(4)
        self.search_entry.connect("search-changed", self._on_search_changed)
        # Escape clears the search box (ROLO-0007); scoped to the entry so it never
        # shadows the dialog/popover Escape handling elsewhere.
        self.search_entry.connect("stop-search", lambda e: e.set_text(""))
        left_box.append(self.search_entry)

        # Category filter (ROLO-0009). Shown only while the vault has categories; its options
        # are rebuilt by _sync_category_filter whenever the category list changes.
        self._category_filter = None  # None = all, "" = uncategorised, else a category name
        self._filter_options: list[str | None] = [None]
        self._syncing_filter = False
        self.category_filter = Gtk.DropDown.new_from_strings(["All categories"])
        self.category_filter.set_margin_start(8)
        self.category_filter.set_margin_end(8)
        self.category_filter.set_margin_bottom(4)
        a11y_label(self.category_filter, "Show category")
        self.category_filter.connect("notify::selected", self._on_category_filter_changed)
        left_box.append(self.category_filter)

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
        # ROLO-0051: one update flow at a time -- a check, its offer dialog, and any download
        # it leads to. Repeated menu clicks used to start N checks and N offers, and two
        # accepted offers raced two downloads onto the same os.replace.
        self._update_busy = False
        self._clipboard_timer_id = 0
        self._clipboard_pending_value = None
        import concurrent.futures

        self._clip_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rolodex-clipboard")
        self._rebuilding = False
        if update_check_enabled() and is_update_supported():
            self._silent_check_id = GLib.timeout_add_seconds(3, self._start_silent_update_check)

        self._current_entry_id = None
        self._collapsed_categories: set[str] = set()
        self._search_debounce_id = 0  # pending GLib timeout for debounced search (ROLO-0018)
        migrate_vault(self.vault)
        # ROLO-0026: reopen on the entry that was open last time. Only its random id is kept in
        # .rolodex.conf -- no name or field -- and an id this vault does not hold is ignored.
        last = conf.get(LAST_ENTRY_KEY)
        self._refresh_list(select_id=last if last in self.vault["entries"] else None)

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
        call _write_vault directly instead, so they can order the write before the assignment.
        """
        try:
            self._write_vault(self.vault, self._key, self.salt)
            return True
        except VaultChangedError:
            self._confirm_overwrite_changed_vault()
            return False
        except OSError as exc:
            self._show_message("Could Not Save", f"The vault was not written to disk: {exc}")
            return False

    def _write_vault(self, vault, key, salt):
        """The one place this window writes the vault file (ROLO-0044).

        Raises VaultChangedError, writing nothing, when the file on disk is not the one this
        session last read or wrote. Every write path -- a save, the password change, a restore --
        comes through here, so none of them can overwrite a change made elsewhere.
        """
        if vault_fingerprint(self.vault_path) != self._disk_fingerprint:
            raise VaultChangedError(self.vault_path)
        save_vault_with_key(vault, key, salt, self.vault_path)
        self._disk_fingerprint = vault_fingerprint(self.vault_path)

    def _confirm_overwrite_changed_vault(self):
        """Something else rewrote the vault since this session read it. Let the user choose
        which copy survives; the default keeps the file on disk."""
        dialog = Adw.AlertDialog(
            heading="Vault Changed Elsewhere",
            body=(
                "The vault file was changed by something else since you unlocked it — another "
                "copy of Rolodex, a sync tool, or a file copied over it. Your latest change was "
                "NOT saved.\n\n"
                "Reload locks Rolodex so you can unlock the version on disk; your unsaved change "
                "is discarded. Overwrite replaces the version on disk with everything open here, "
                "which discards the other change instead."
            ),
        )
        dialog.add_response("reload", "Reload From Disk")
        dialog.add_response("overwrite", "Overwrite")
        dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("reload")
        dialog.set_close_response("reload")
        dialog.connect("response", self._on_changed_vault_response)
        dialog.present(self)

    def _on_changed_vault_response(self, _dialog, response):
        if self.vault is None:
            return  # locked while the dialog was open
        if response == "overwrite":
            self._disk_fingerprint = vault_fingerprint(self.vault_path)
            if self._save():
                self._toast("Saved over the changed vault")
        else:
            self._lock()

    def _on_close_request(self, *_args):
        self._cancel_search_debounce()
        self._cancel_totp_tick()  # covers _lock too, which routes through close()
        self._cancel_pending_update()
        if self._vault_lock is not None:
            self._vault_lock.release()  # covers _lock too, which routes through close()
        # Quitting must not leave a copied secret behind any more than locking may (ROLO-0034).
        # A no-op after _lock, which already queued the wipe.
        self._clear_clipboard_on_lock()
        self._clip_pool.shutdown(wait=False)  # queued wipes still run; see _clear_clipboard_on_lock
        if self.vault is not None:  # _lock has already recorded it and cleared the vault
            save_config({LAST_ENTRY_KEY: self._current_entry_id})
        save_config({
            "window_width": self.get_width(),
            "window_height": self.get_height(),
            "window_maximized": self.is_maximized(),
        })
        return False  # allow the window to close

    # ------------------------------------------------------------------
    # Sidebar list
    # ------------------------------------------------------------------

    def _sync_category_filter(self, categories):
        """Rebuild the filter's options to match the vault's categories, keeping the current
        choice where it still exists and falling back to "All categories" where it does not."""
        options: list[str | None] = [None, *categories, ""]
        self.category_filter.set_visible(bool(categories))
        if self._category_filter not in options:
            self._category_filter = None
        if options != self._filter_options:
            self._syncing_filter = True
            labels = ["All categories", *categories, "Uncategorised"]
            self.category_filter.set_model(Gtk.StringList.new(labels))
            self._filter_options = options
            self._syncing_filter = False
        self._syncing_filter = True
        self.category_filter.set_selected(options.index(self._category_filter))
        self._syncing_filter = False

    def _on_category_filter_changed(self, dropdown, _pspec):
        if self._syncing_filter or self.vault is None:
            return
        pos = dropdown.get_selected()
        if 0 <= pos < len(self._filter_options):
            self._category_filter = self._filter_options[pos]
            self._refresh_list()

    def _refresh_list(self, select_id=None):
        query = self.search_entry.get_text().strip()
        categories = self.vault.get("categories", [])
        self._sync_category_filter(categories)
        cat_filter = self._category_filter if categories else None

        # Clear list. gtk_list_box_remove emits ::row-selected(NULL) for the selected row, so
        # _on_row_selected would otherwise wipe _current_entry_id on every rebuild -- before the
        # re-selection pass below ever gets to look for it (search.md INV-7).
        self._rebuilding = True
        clear_container(self.listbox)

        select_row = None
        total = len(self.vault["entries"])

        if query or cat_filter is not None:
            # Search or a category filter active: flat list, no grouping
            entries = search_entries(self.vault, query, category=cat_filter)
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
            category = field_category(field["label"])
            row.add_css_class(f"field-{category}")

            # The colour bar above is one cue; this icon is the same fact in a form that
            # survives greyscale and colourblindness (ROLO-0016).
            cue_icon, cue_name = FIELD_CATEGORY_CUES[category]
            row.add_prefix(a11y_label(Gtk.Image(icon_name=cue_icon, valign=Gtk.Align.CENTER,
                                                tooltip_text=cue_name), cue_name))

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
                # Otherwise a screen reader spells out eight bullet characters (ROLO-0017).
                a11y_label(val_label, "Hidden value")
            elif is_sensitive and self._revealed:
                val_label.add_css_class("field-revealed-sensitive")
            row.add_suffix(val_label)

            # Copy button
            copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                                  tooltip_text=f"Copy {field['label']}")
            a11y_label(copy_btn, f"Copy {field['label']}")
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

    def _check_clock(self):
        GLib.idle_add(self._set_clock_synced, clock_synchronized())

    def _set_clock_synced(self, synced):
        self._clock_synced = synced
        if synced is False and self._totp_widgets and self._current_entry_id:
            self._show_detail(self._current_entry_id)  # add the warning to rows already drawn
        return False

    def _build_totp_row(self, cfg):
        """A 'Code' row: grouped live digits, a depleting ring, seconds left, and copy."""
        state = {"code": "", "fraction": 1.0}
        row = Adw.ActionRow()
        row.set_title("Code")
        row.add_css_class("totp-row")
        # Decorative, and hidden from screen readers on purpose: the row's own "Code" title
        # already says what this is. It exists so the row keeps its left edge with the field
        # rows around it, which carry a type icon since ROLO-0016.
        row.add_prefix(Gtk.Image(icon_name=FIELD_CATEGORY_CUES["credential"][0],
                                 valign=Gtk.Align.CENTER,
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION))
        if self._clock_synced is False:
            # RFC 6238 needs both sides to agree on the time. A drifted clock makes every code
            # wrong, and it looks like "the site rejected my code" (ROLO-0068).
            row.set_subtitle("Your computer's clock is not synchronised, so this code may be "
                             "rejected. Turn on automatic time in your system settings.")

        code_label = Gtk.Label(valign=Gtk.Align.CENTER, selectable=True)
        code_label.add_css_class("totp-code")
        row.add_suffix(code_label)

        ring = Gtk.DrawingArea(valign=Gtk.Align.CENTER)
        ring.add_css_class("totp-ring")
        ring.set_content_width(18)
        ring.set_content_height(18)
        ring.set_draw_func(self._draw_totp_ring, state)
        row.add_suffix(ring)

        rem_label = Gtk.Label(valign=Gtk.Align.CENTER)
        rem_label.add_css_class("totp-remaining")
        row.add_suffix(rem_label)

        copy_btn = a11y_label(Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                                         tooltip_text="Copy 2FA code"), "Copy 2FA code")
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

    def _draw_totp_ring(self, area, cr, width, height, state):
        """Draw a ring that empties clockwise from the top as the code's window elapses.

        Both strokes use the ring's CSS colour (.totp-ring), so it follows the theme's accent."""
        frac = state.get("fraction", 1.0)
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 2
        color = area.get_color()
        cr.set_line_width(2.5)
        cr.set_source_rgba(color.red, color.green, color.blue, 0.2)  # faint full-circle track
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgba(color.red, color.green, color.blue, 0.95)  # remaining arc
        start = -math.pi / 2
        cr.arc(cx, cy, radius, start, start + frac * 2 * math.pi)
        cr.stroke()

    def _clipboard_submit(self, fn, *args, then=None):
        """Run a clipboard helper on this window's single clipboard worker (ROLO-0046).

        The helpers shell out with a five-second timeout, so on the GTK main thread a hung
        wl-paste froze the window for up to ten seconds. ONE worker, not a thread per call, so
        a copy and the clear that follows it can never run out of order. *then* receives the
        result back on the main thread.
        """
        future = self._clip_pool.submit(fn, *args)
        if then is not None:
            future.add_done_callback(lambda f: GLib.idle_add(then, f.result()))
        return future

    def _copy_value(self, value, label):
        """Copy a secret to the clipboard with the auto-clear timer + toast (ROLO-0003)."""
        # Remember what was copied whatever the delay: with clipboard_clear_seconds = 0 there is
        # no timer at all, and a lock still has to be able to wipe it (ROLO-0003). Set before
        # the copy runs, so a lock that lands mid-copy still queues the wipe behind it.
        self._cancel_clipboard_timer()
        self._clipboard_pending_value = value
        self._clipboard_submit(copy_to_clipboard, value,
                               then=lambda ok: self._after_copy(ok, value, label))

    def _after_copy(self, ok, value, label):
        if self.vault is None:
            return False  # locked while the copy ran; the lock already queued the wipe
        if not ok:
            if self._clipboard_pending_value == value:
                self._clipboard_pending_value = None
            self._toast("Clipboard not available")
            return False
        if self._clipboard_pending_value != value:
            return False  # a later copy superseded this one
        delay = self._clipboard_clear_s
        if delay > 0:
            self._toast(f"Copied {label} — clipboard clears in {delay}s")
            self._clipboard_timer_id = GLib.timeout_add_seconds(
                delay, self._clear_clipboard_if_unchanged, value
            )
        else:
            self._toast(f"Copied {label}")
        return False

    def _make_copy_handler(self, value, label):
        return lambda _btn: self._copy_value(value, label)

    def _clear_clipboard_if_unchanged(self, value):
        """The auto-clear timer: queue the wipe on the clipboard worker (ROLO-0003, ROLO-0046)."""
        self._clipboard_timer_id = 0  # this source removes itself via the False return below
        self._clipboard_pending_value = None
        self._clipboard_submit(clear_clipboard_if_unchanged, value)
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
        # The worker's thread is not a daemon, so the interpreter joins it on exit and a wipe
        # queued here still runs when this lock is really the app quitting.

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

    def _set_update_busy(self, busy):
        """Claim or release the single update flow (ROLO-0051), greying the menu item while it
        runs. Returns False so it can be scheduled with GLib.idle_add from a worker."""
        self._update_busy = busy
        action = self.lookup_action("check-updates")
        if action is not None:
            action.set_enabled(not busy)
        return False

    def _on_toggle_auto_updates(self, action, value):
        """Turn the automatic check on or off (INV-1). The only in-app writer of the preference."""
        enabled = bool(value.get_boolean())
        if not set_update_check_enabled(enabled):
            # ROLO-0063: the write was swallowed, so the setting did not change. Say so and
            # leave the checkbox where it was, rather than toasting a preference that the
            # next launch will not have.
            self._toast("Couldn't save that setting — the settings file is not writable")
            return
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
        if self._update_busy:
            return False
        self._set_update_busy(True)
        threading.Thread(target=self._silent_update_worker, daemon=True).start()
        return False

    def _silent_update_worker(self):
        """The unforced check. Every failure yields None and is never surfaced (INV-13) --
        this path runs without the user asking, so it must never interrupt them."""
        info = check_for_update()
        if info is not None:
            GLib.idle_add(self._offer_update, info)
        else:
            GLib.idle_add(self._set_update_busy, False)

    def _offer_update(self, info):
        """Present the offer. The update flow stays claimed until the user answers it."""
        if self._update_cancelled:
            return self._set_update_busy(False)
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
        if self._update_busy:
            return  # a check, an offer or a download is already running (ROLO-0051)
        import threading

        self._set_update_busy(True)
        self._toast("Checking for updates...")
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_worker(self):
        """Background half of the manual check. Never touches GTK directly."""
        try:
            info = check_for_update(force=True)
        except UpdateError as exc:
            # INV-13: a forced failure must not read as "you're up to date".
            GLib.idle_add(self._toast, f"Couldn't check for updates: {exc}")
            GLib.idle_add(self._set_update_busy, False)
            return
        GLib.idle_add(self._finish_check_updates, info)

    def _finish_check_updates(self, info):
        if info is None:
            self._set_update_busy(False)
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
            GLib.idle_add(self._set_update_busy, False)
            return
        except UpdateError as exc:
            GLib.idle_add(self._show_message, "Update failed", str(exc))
            GLib.idle_add(self._set_update_busy, False)
            return
        # INV-15: the window may have been locked or closed while this ran. Drop the download
        # rather than installing something the user is no longer consenting to.
        if self._update_cancelled:
            try:
                os.unlink(staged)
            except OSError:
                pass
            GLib.idle_add(self._set_update_busy, False)
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
            self._set_update_busy(False)
            return
        try:
            apply_update(staged, on_before_exec=self._wipe_secrets_for_update)
        except UpdateError as exc:
            self._show_message("Update failed", str(exc))
            self._set_update_busy(False)

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

    def _on_preferences(self, *_args):
        themes = getattr(self.get_application(), "themes", None)
        if themes is not None:
            PreferencesDialog(themes).present(self)

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
        # With an AdwDialog open, Gtk.Window.close() closes the DIALOG and leaves the window up
        # (measured on libadwaita 1.9). So an idle lock that fired while an editor was open left
        # the entry list on screen behind the unlock dialog, and never released the vault lock.
        # force_close skips an editor's unsaved-changes prompt, which is right for a lock.
        while (dialog := self.get_visible_dialog()) is not None:
            dialog.force_close()
        # Drop the rendered entry as well: detail_box holds the last-viewed values as label text
        # and one copy closure per field, so clearing self.vault alone leaves them reachable.
        save_config({LAST_ENTRY_KEY: self._current_entry_id})  # ROLO-0026
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
        if self._vault_lock is not None:
            self._vault_lock.release()  # before the unlock screen can try to take it again
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
        if len(parsed) > MAX_IMPORT_ENTRIES:
            # The preview builds one row per entry up front, so an enormous file froze the UI in
            # its constructor (ROLO-0070).
            self._show_message(
                "Import",
                f"That file holds {len(parsed)} entries. Import at most {MAX_IMPORT_ENTRIES} at "
                "a time — split the file and import each part.",
            )
            return

        # Show preview dialog
        dialog = ImportPreviewDialog(self, parsed, filepath)
        dialog.present(self)

    def _finish_import(self, parsed, category=""):
        if self.vault is None:
            return  # locked while the preview dialog was open
        if category and category not in self.vault["categories"]:
            category = ""  # deleted while the preview was open
        # The preview hands over exactly the rows the user ticked, duplicates included, so
        # nothing is skipped here: a ticked row that did not import was ROLO-0047.
        imported, _skipped = import_entries(self.vault, parsed, skip_duplicates=False,
                                            category=category)
        self._save()
        self._refresh_list()
        self._toast(f"Imported {imported} {entries_noun(imported)}.")

    # ------------------------------------------------------------------
    # Backup (encrypted copy)
    # ------------------------------------------------------------------

    def _on_backup(self, *_args):
        # Save latest state first
        self._save()

        save_dialog = Gtk.FileDialog()
        save_dialog.set_title("Backup vault to...")
        default_name = f"contacts_backup_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}.vault"
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
            self._write_vault(vault, key, salt)
        except (OSError, VaultChangedError) as err:
            exc = (
                "the vault file was changed elsewhere since you unlocked it"
                if isinstance(err, VaultChangedError) else err
            )
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
        default_name = f"rolodex_export_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}.txt"
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
            self._write_vault(self.vault, new_key, new_salt)
        except (OSError, VaultChangedError) as err:
            exc = (
                "the vault file was changed elsewhere since you unlocked it"
                if isinstance(err, VaultChangedError) else err
            )
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
            self.vault["entries"][entry_id]["modified"] = now_iso()
            self._save()
            self._refresh_list()
            if self._current_entry_id == entry_id:
                self._show_detail(entry_id)

    def _move_entry_to_category_idle(self, entry_id, category):
        if self.vault is not None:  # locked between the drop and this callback
            self._move_entry_to_category(entry_id, category)
        return False

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
        handle.set_tooltip_text("Drag to reorder, or Ctrl+Up / Ctrl+Down")
        box.append(handle)

        # A placeholder is not a name: once the box has text a screen reader has nothing to
        # say what the box is for (ROLO-0017).
        self.label_entry = a11y_label(
            Gtk.Entry(placeholder_text="Label", text=label, hexpand=True), "Field label")
        self.label_entry.set_size_request(110, -1)
        box.append(self.label_entry)

        self.value_entry = a11y_label(
            Gtk.Entry(placeholder_text="Value", text=value, hexpand=True), "Field value")
        self.value_entry.set_size_request(160, -1)
        box.append(self.value_entry)

        # Latches once the user toggles "Hide" by hand, after which the label no longer drives
        # sensitivity (see on_label_changed).
        self._sens_user_set = False

        if sensitive is None:
            sensitive = is_sensitive_label(label)

        # Password generator (ROLO-0004): only offered on sensitive fields, since generating a
        # strong secret only makes sense for passwords/keys.
        self.gen_btn = a11y_label(Gtk.MenuButton(icon_name="view-refresh-symbolic",
                                                 tooltip_text="Generate a strong password"),
                                  "Generate a strong password")
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

        remove_btn = a11y_label(
            Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text="Remove field"), "Remove field")
        remove_btn.add_css_class("flat")
        remove_btn.add_css_class("error")
        remove_btn.connect("clicked", lambda b: self.dialog._remove_field_row(self))
        box.append(remove_btn)

        self.set_child(box)
        attach_reorder_keys(self, self.dialog._reorder_field, self.label_entry.grab_focus)

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
        self.name_entry.connect("changed", lambda e: e.remove_css_class("error"))
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
        self.notes_view = a11y_label(Gtk.TextView(), "Notes")
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
            self._close_wiped()
            return
        self._confirm(
            "Discard changes?",
            "This entry has unsaved changes. Discard them?",
            "Discard", Adw.ResponseAppearance.DESTRUCTIVE, self._close_wiped,
        )

    def _close_wiped(self):
        """Close, clearing the secrets the row buffers still hold (ROLO-0059). Every close
        routes through here. _on_save has already read every value into its own list before
        _commit runs, so the wipe cannot reach what is being saved."""
        for row in self._get_field_rows():
            row.value_entry.set_text("")
        self.force_close()

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
        """Move dragged_row to the position of target_row (see move_item)."""
        current = self._get_field_rows()
        if dragged_row not in current or target_row not in current:
            return
        rows = move_item(current, dragged_row, target_row)

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
            # INV-6 forbids a nameless entry; say so rather than leave Save looking dead
            # (ROLO-0070). The highlight clears as soon as the name is edited.
            self.name_entry.add_css_class("error")
            self.name_entry.grab_focus()
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
        self._close_wiped()  # bypass the unsaved-changes guard — this is a deliberate save


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
        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("error")
        self.status.set_visible(False)
        vbox.append(self.status)

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

        # ROLO-0067: one target category for the whole import, "No category" by default.
        self.categories = list(main_win.vault["categories"])
        cat_row = Adw.ComboRow(title="Add to category")
        cat_row.set_model(Gtk.StringList.new(["No category", *self.categories]))
        cat_group = Gtk.ListBox()
        cat_group.set_selection_mode(Gtk.SelectionMode.NONE)
        cat_group.add_css_class("boxed-list")
        cat_group.append(cat_row)
        self.cat_row = cat_row
        vbox.append(cat_group)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        # Same rule the importer would skip by, covering duplicates WITHIN the file too. A
        # duplicate starts unticked; ticking it imports it as a second entry (ROLO-0047).
        dup_flags = duplicate_flags(main_win.vault, parsed)

        for i, entry in enumerate(parsed):
            is_dup = dup_flags[i]
            row = Adw.ActionRow()
            row.set_title(GLib.markup_escape_text(entry["name"]))
            field_count = len(entry["fields"])
            notes_flag = " +notes" if entry.get("notes") else ""
            subtitle = f"{field_count} fields{notes_flag}"
            if is_dup:
                subtitle += "  (duplicate name — tick to import it anyway)"
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
            self.status.set_text("Tick at least one entry to import.")  # ROLO-0070
            self.status.set_visible(True)
            return
        pos = self.cat_row.get_selected()
        category = self.categories[pos - 1] if pos >= 1 else ""
        self.main_win._finish_import(selected, category)
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

        # Enter submits, as it does in the unlock and restore dialogs (ROLO-0070).
        for row in (self.current_pw, self.new_pw, self.confirm_pw):
            row.connect("entry-activated", self._on_save)

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
        except ValueError as e:
            # Our own messages ("Not a valid vault file", "truncated or corrupt") carry no path.
            GLib.idle_add(self._unlock_fail, str(e))
        except OSError:
            # An OSError's text carries the full path of the file, and this dialog is not the
            # place to print it (ROLO-0072).
            GLib.idle_add(self._unlock_fail, "Could not read that backup file.")
        except Exception:  # noqa: BLE001 - anything else would leave the dialog on "Decrypting..."
            GLib.idle_add(self._unlock_fail, "Could not restore that backup file.")

    def _unlock_ok(self, vault, salt, pw, key):
        # The KDF runs on a background thread and Cancel/Esc only closes this dialog -- it does
        # not cancel or disown the thread. Without this check a cancelled restore still landed
        # and overwrote the live vault, which is the one operation here that cannot be undone.
        if not self.get_presented():
            return
        self.main_win._finish_restore(vault, salt, pw, key)
        self.pw_entry.set_text("")  # ROLO-0059: the backup password is handed over by now
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
        handle.set_tooltip_text("Drag to reorder, or Ctrl+Up / Ctrl+Down")
        box.append(handle)

        # Category name label
        self.name_label = Gtk.Label(label=name, xalign=0, hexpand=True)
        self.name_label.set_ellipsize(3)
        box.append(self.name_label)

        # Count badge
        count_lbl = a11y_label(Gtk.Label(label=str(count)), f"{count} {entries_noun(count)}")
        count_lbl.add_css_class("category-count")
        box.append(count_lbl)

        # Rename button
        rename_btn = a11y_label(Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Rename"),
                                f"Rename category {name}")
        rename_btn.add_css_class("flat")
        rename_btn.connect("clicked", lambda b: self.dialog._rename_category(self))
        box.append(rename_btn)

        # Delete button
        del_btn = a11y_label(Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text="Delete"),
                             f"Delete category {name}")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("error")
        del_btn.connect("clicked", lambda b: self.dialog._delete_category(self))
        box.append(del_btn)

        self.set_child(box)
        # _reorder_category rebuilds the list, so this row is gone by the time focus returns.
        attach_reorder_keys(self, self.dialog._reorder_category,
                            lambda: self.dialog._focus_category(name))

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
        self.new_cat_entry = a11y_label(
            Gtk.Entry(placeholder_text="New category name...", hexpand=True), "New category name")
        self.new_cat_entry.connect("activate", lambda e: self._add_category())
        add_box.append(self.new_cat_entry)
        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda b: self._add_category())
        add_box.append(add_btn)
        vbox.append(add_box)

        # ROLO-0070: why an add or a rename did nothing, instead of silence.
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.add_css_class("error")
        self.status.set_visible(False)
        vbox.append(self.status)

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

    def _focus_category(self, name):
        for row in self.cat_listbox:
            if isinstance(row, CategoryRow) and row.cat_name == name:
                row.grab_focus()
                return

    def _show_status(self, msg):
        self.status.set_text(msg)
        self.status.set_visible(bool(msg))

    def _add_category(self):
        name = self.new_cat_entry.get_text().strip()
        if not name:
            self._show_status("Type a name for the new category.")
            return
        if not add_category(self.main_win.vault, name):
            self._show_status(f"A category named “{name}” already exists.")
            return
        self._show_status("")
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
                if not new_name:
                    self._show_status("A category name cannot be empty.")
                elif new_name != row.cat_name and new_name in self.main_win.vault["categories"]:
                    self._show_status(
                        f"A category named “{new_name}” already exists. Categories cannot be "
                        "merged by renaming — move the entries, then delete the empty one.")
                elif new_name != row.cat_name:
                    self._show_status("")
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
        cats[:] = move_item(cats, dragged_row.cat_name, target_row.cat_name)
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
        if response == "update":
            self.main_win._start_update_download(self.info)  # keeps the flow claimed
            return
        if response == "skip":
            skip_update_version(self.info.version)
            self.main_win._toast(f"Skipping Rolodex {self.info.version}")
        self.main_win._set_update_busy(False)


# ===========================================================================
# Application
# ===========================================================================


CUSTOM_CSS = """
/* Every colour here is a palette name (@rolo_*) defined by theme_css() (ROLO-0015). */

/* ══════════════════════════════════════════════
   Gradient backgrounds
   ══════════════════════════════════════════════ */

.main-paned {
    background-image: linear-gradient(160deg, @rolo_bg_1 0%, @rolo_bg_2 35%, @rolo_bg_3 65%, @rolo_bg_1 100%);
}

.sidebar-box {
    background-image: linear-gradient(180deg, @rolo_sidebar_1 0%, @rolo_sidebar_2 100%);
    border-right: 1px solid alpha(@rolo_accent, 0.08);
}

/* Unlock window */
window.background {
    background-image: linear-gradient(160deg, @rolo_bg_1 0%, @rolo_bg_dialog 50%, @rolo_bg_1 100%);
}

/* ══════════════════════════════════════════════
   Glass effect for cards & panels
   ══════════════════════════════════════════════ */

/* Boxed lists (field cards, import list, password rows) */
.boxed-list {
    background: @rolo_card_bg;
    border: 1px solid @rolo_card_border;
    border-radius: 12px;
    box-shadow:
        0 4px 16px @rolo_shadow,
        inset 0 1px 0 @rolo_highlight;
}

.boxed-list row {
    background: transparent;
    border-bottom: 1px solid @rolo_row_line;
}

.boxed-list row:last-child {
    border-bottom: none;
}

/* ── Field category left-border colours (checked against colourblindness simulations) ── */
.field-credential { border-left: 3px solid @rolo_field_credential; }
.field-key        { border-left: 3px solid @rolo_field_key; }
.field-identity   { border-left: 3px solid @rolo_field_identity; }
.field-url        { border-left: 3px solid @rolo_field_url; }
.field-date       { border-left: 3px solid @rolo_field_date; }
.field-other      { border-left: 3px solid @rolo_field_other; }

/* Notes frame: glass card, in its own colour apart from the field categories */
.notes-frame {
    background: @rolo_notes_bg;
    border: 1px solid alpha(@rolo_notes, 0.25);
    border-left: 3px solid @rolo_notes;
    border-radius: 10px;
    padding: 4px 8px;
    box-shadow:
        0 2px 12px @rolo_shadow_soft,
        inset 0 1px 0 @rolo_highlight;
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
    background: @rolo_hover;
}

.navigation-sidebar row:selected {
    background: alpha(@rolo_accent, 0.15);
    border-left: 3px solid @rolo_accent;
    box-shadow: inset 0 1px 0 @rolo_highlight;
}

/* Action buttons: glass pill style */
.reveal-btn, .edit-btn {
    background: @rolo_pill_bg;
    border: 1px solid @rolo_pill_border;
    border-radius: 8px;
    box-shadow: 0 2px 8px @rolo_shadow_soft;
    padding: 6px 14px;
    transition: background 150ms ease, border-color 150ms ease;
}

.reveal-btn:hover {
    background: @rolo_reveal_hover_bg;
    border-color: @rolo_reveal_hover_border;
}

.edit-btn:hover {
    background: alpha(@rolo_accent, 0.1);
    border-color: alpha(@rolo_accent, 0.3);
}

/* Search entry: glass style */
.sidebar-box searchentry {
    background: @rolo_card_bg;
    border: 1px solid @rolo_card_border;
    border-radius: 8px;
    box-shadow: inset 0 1px 0 @rolo_highlight;
}

.sidebar-box searchentry:focus-within {
    background: @rolo_card_bg_strong;
    border-color: alpha(@rolo_accent, 0.5);
    box-shadow:
        inset 0 1px 0 @rolo_highlight,
        0 0 0 2px alpha(@rolo_accent, 0.2);
}

/* ══════════════════════════════════════════════
   Text & colour accents
   ══════════════════════════════════════════════ */

/* Entry name in detail view */
.entry-title {
    color: @rolo_accent_text;
    text-shadow: 0 0 20px alpha(@rolo_glow, 0.3);
}

/* Sensitive field mask */
.field-masked {
    color: @rolo_masked;
    font-style: italic;
    letter-spacing: 2px;
}

/* Revealed sensitive value */
.field-revealed-sensitive {
    color: @rolo_revealed;
    text-shadow: 0 0 12px alpha(@rolo_glow, 0.15);
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
    color: @rolo_accent_text;
}
.totp-remaining {
    font-size: 0.85em;
    color: @rolo_muted;
    min-width: 26px;
}
/* The countdown ring draws in its CSS colour (_draw_totp_ring). */
.totp-ring {
    color: @rolo_accent_text;
}

/* Copy button */
.copy-btn {
    border-radius: 6px;
    transition: color 150ms ease, background 150ms ease;
}

.copy-btn:hover {
    color: @rolo_accent_text;
    background: alpha(@rolo_accent, 0.1);
}

/* Timestamp styling */
.timestamp {
    color: @rolo_dim;
    font-size: 0.85em;
}

/* Reveal button */
.reveal-btn {
    color: @rolo_revealed;
}

/* Edit button */
.edit-btn {
    color: @rolo_accent_text;
}

/* Count label */
.count-label {
    color: @rolo_accent_text;
    font-weight: bold;
    text-shadow: 0 0 16px alpha(@rolo_glow, 0.2);
}

/* Unlock dialog title */
.unlock-title {
    color: @rolo_accent_text;
    font-size: 1.6em;
    font-weight: 800;
    text-shadow: 0 0 24px alpha(@rolo_glow, 0.35);
}

/* Separator gets a subtle glow */
separator {
    background: linear-gradient(90deg,
        transparent 0%,
        alpha(@rolo_accent, 0.3) 50%,
        transparent 100%);
    min-height: 1px;
}

/* Header bar: blend with gradient */
headerbar {
    background: @rolo_headerbar_bg;
    border-bottom: 1px solid @rolo_headerbar_border;
    box-shadow: 0 1px 4px @rolo_shadow_soft;
}

/* Suggested-action buttons (Create Vault, Unlock, Save, Import) */
button.suggested-action {
    background: linear-gradient(135deg, @rolo_accent_bg_dark 0%, @rolo_accent_bg 100%);
    color: #ffffff;
    border: 1px solid alpha(@rolo_accent, 0.3);
    box-shadow:
        0 2px 8px alpha(@rolo_glow, 0.3),
        inset 0 1px 0 alpha(#ffffff, 0.1);
}

button.suggested-action:hover {
    background: linear-gradient(135deg, @rolo_accent_bg 0%, @rolo_accent_bg 100%);
    box-shadow:
        0 4px 16px alpha(@rolo_glow, 0.4),
        inset 0 1px 0 alpha(#ffffff, 0.12);
}

/* Destructive button glow */
button.destructive-action {
    box-shadow: 0 2px 8px @rolo_destructive_glow;
}

button.destructive-action:hover {
    box-shadow: 0 4px 16px @rolo_destructive_glow_strong;
}

/* Password entry rows: blend with glass */
row.entry {
    background: transparent;
}

/* ── Field editor (Add/Edit dialog) ── */
.field-editor-list {
    background: @rolo_notes_bg;
}

.field-editor-list row {
    background: transparent;
    border-bottom: 1px solid @rolo_row_line;
    transition: background 150ms ease;
}

.field-editor-list row:hover {
    background: @rolo_hover_soft;
}

/* ── Category header rows in sidebar ── */
.category-header-row {
    background: transparent;
}

.category-header-row:hover {
    background: @rolo_hover_soft;
}

.navigation-sidebar .category-header-row:selected {
    background: transparent;
    border-left: none;
    box-shadow: none;
}

.category-header-label {
    color: @rolo_muted;
    font-size: 0.75em;
    font-weight: 800;
    letter-spacing: 1.5px;
}

.category-count {
    background: @rolo_count_bg;
    border-radius: 10px;
    color: @rolo_muted;
    font-size: 0.75em;
    font-weight: 600;
    min-width: 20px;
    padding: 1px 6px;
}

.category-drop-hover {
    background: alpha(@rolo_accent, 0.15);
    border-radius: 8px;
    box-shadow: 0 0 8px alpha(@rolo_accent, 0.3);
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

    SHORTCUTS: ClassVar[list[tuple[str, str]]] = [
        ("<Control>f", "Focus search"),
        ("<Control>n", "Add entry"),
        ("<Control><Shift>c", "Copy password / secret"),
        ("<Control>l", "Lock vault"),
        ("Escape", "Clear search"),
        ("<Control>question", "Keyboard shortcuts"),
        ("<Control>comma", "Preferences"),
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


class ThemeManager:
    """Owns the app's one stylesheet and redraws it when the theme, the accent or the desktop's
    light/dark setting changes (ROLO-0015). Automatic follows the desktop live; Dark, Light and
    High contrast force libadwaita's own widgets to match."""

    def __init__(self, display):
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.style = Adw.StyleManager.get_default()
        conf = load_config()
        self.theme = config_choice(conf, THEME_KEY, THEMES, DEFAULT_THEME)
        self.accent = config_choice(conf, ACCENT_KEY, (ACCENT_SYSTEM, *ACCENT_PRESETS),
                                    DEFAULT_ACCENT)
        self.palette = ""
        # Kept so a test can detach from the process-wide StyleManager again.
        self.handlers = [self.style.connect("notify::dark", lambda *_: self._restyle())]
        if self.follows_system_accent_supported():
            self.handlers.append(
                self.style.connect("notify::accent-color-rgba", lambda *_: self._restyle()))
        self.apply()

    def follows_system_accent_supported(self) -> bool:
        """Whether the desktop hands over an accent colour at all. libadwaita before 1.6 has no
        accent API, and a desktop without the portal setting reports no support."""
        return (hasattr(self.style, "get_system_supports_accent_colors")
                and self.style.get_system_supports_accent_colors())

    def accent_hex(self) -> str:
        if self.accent in ACCENT_PRESETS:
            return ACCENT_PRESETS[self.accent]
        if self.follows_system_accent_supported():
            rgba = self.style.get_accent_color_rgba()
            return _rgb_hex((rgba.red, rgba.green, rgba.blue))
        return FALLBACK_ACCENT

    def apply(self) -> None:
        scheme = {"auto": Adw.ColorScheme.DEFAULT,
                  "light": Adw.ColorScheme.FORCE_LIGHT}.get(self.theme, Adw.ColorScheme.FORCE_DARK)
        if self.style.get_color_scheme() != scheme:
            self.style.set_color_scheme(scheme)  # fires notify::dark when the result changes
        self._restyle()

    def _restyle(self) -> None:
        self.palette = resolve_palette(self.theme, self.style.get_dark())
        css_vars = (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 16)
        self.provider.load_from_string(
            theme_css(self.palette, self.accent_hex(), css_vars))

    def set_theme(self, theme: str) -> bool:
        """Switch theme now and remember it. False when the config write failed."""
        self.theme = theme
        self.apply()
        return save_config({THEME_KEY: theme})

    def set_accent(self, accent: str) -> bool:
        self.accent = accent
        self._restyle()
        return save_config({ACCENT_KEY: accent})


class PreferencesDialog(Adw.PreferencesDialog):
    """Appearance settings (ROLO-0015): the theme and the accent colour. Each change applies
    at once and is saved to .rolodex.conf."""

    ACCENT_CHOICES: ClassVar[list[tuple[str, str]]] = (
        [(ACCENT_SYSTEM, "Follow desktop")] + [(k, k.capitalize()) for k in ACCENT_PRESETS])

    def __init__(self, themes: ThemeManager):
        super().__init__(title="Preferences")
        self._themes = themes
        page = Adw.PreferencesPage(title="Appearance", icon_name="preferences-desktop-symbolic")
        group = Adw.PreferencesGroup(title="Appearance")

        theme_keys = list(THEMES)
        self.theme_row = Adw.ComboRow(
            title="Theme", subtitle="Automatic follows your desktop's light or dark setting",
            model=Gtk.StringList.new(list(THEMES.values())))
        self.theme_row.set_selected(theme_keys.index(themes.theme))
        self.theme_row.connect(
            "notify::selected",
            lambda row, _p: self._saved(themes.set_theme(theme_keys[row.get_selected()])))
        group.add(self.theme_row)

        accent_keys = [k for k, _ in self.ACCENT_CHOICES]
        subtitle = ("" if themes.follows_system_accent_supported() else
                    "Your desktop does not share an accent colour, so Follow desktop uses blue")
        self.accent_row = Adw.ComboRow(
            title="Accent colour", subtitle=subtitle,
            model=Gtk.StringList.new([label for _, label in self.ACCENT_CHOICES]))
        self.accent_row.set_selected(accent_keys.index(themes.accent))
        self.accent_row.connect(
            "notify::selected",
            lambda row, _p: self._saved(themes.set_accent(accent_keys[row.get_selected()])))
        group.add(self.accent_row)

        page.add(group)
        self.add(page)

    def _saved(self, ok: bool) -> None:
        if not ok:
            self.add_toast(Adw.Toast(
                title="Could not save this setting. It applies until Rolodex closes."))


class RolodexApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.vault_path = VAULT_FILE
        self.themes = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        # INV-15: remove update temps orphaned by a download whose process did not outlive it.
        sweep_stale_update_temps()
        display = Gdk.Display.get_default()
        if display is None:
            # No display to style. Passing None on raised a TypeError that said nothing about
            # the cause (ROLO-0073); GTK itself reports the missing display when a window opens.
            print("Rolodex: no display available", file=sys.stderr)
            return
        self.themes = ThemeManager(display)

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

    def open_main(self, vault, salt, password, vault_path, key=None, lock=None):
        win = MainWindow(self, vault, salt, password, vault_path, key, lock)
        win.present()


# ROLO-0049: the oldest toolkit this code runs on, each set by an API it calls.
# libadwaita 1.5: Adw.Dialog, Adw.AlertDialog, force_close, get_visible_dialog.
# GTK 4.12: Gtk.CssProvider.load_from_string.
MIN_ADW = (1, 5)
# ROLO-0088: typelibs a frozen build must carry itself (checked by --selftest).
BUNDLED_TYPELIBS = ("Gtk-4.0.typelib", "Gdk-4.0.typelib", "Gsk-4.0.typelib", "Adw-1.typelib")
MIN_GTK = (4, 12)


def toolkit_too_old() -> str | None:
    """A message naming what is too old, or None. Below these the app died with an
    AttributeError at the first missing call -- before any window, with nothing saying why."""
    adw = (Adw.get_major_version(), Adw.get_minor_version())
    gtk = (Gtk.get_major_version(), Gtk.get_minor_version())
    problems = []
    if gtk < MIN_GTK:
        problems.append(f"GTK {'.'.join(map(str, MIN_GTK))} (found {gtk[0]}.{gtk[1]})")
    if adw < MIN_ADW:
        problems.append(f"libadwaita {'.'.join(map(str, MIN_ADW))} (found {adw[0]}.{adw[1]})")
    if not problems:
        return None
    return "Rolodex needs " + " and ".join(problems) + " or newer."


def main():
    too_old = toolkit_too_old()
    if too_old:
        print(too_old, file=sys.stderr)
        sys.exit(1)
    if "--selftest" in sys.argv[1:]:
        # Packaging smoke test. Reaching this line means every module-level import — including
        # `from gi.repository import Adw, Gdk, Gio, GLib, Gtk` (which loads the GTK/libadwaita
        # typelibs + shared libraries) and `cryptography` — succeeded, so the bundled runtime is
        # intact on this OS. CI runs the built binary with --selftest to fail any build whose
        # GTK stack didn't bundle correctly. Exits without starting the GUI (no display needed).
        # A frozen build must carry its OWN GTK 4 typelibs. The import above can succeed on
        # the build machine by finding the host's, which is how binaries that bundled GTK 3.0
        # and no GTK 4 passed this test on the Ubuntu runner and crashed on openSUSE
        # (ROLO-0088). Checking the bundle is what makes this test mean "portable".
        if getattr(sys, "frozen", False):
            bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "gi_typelibs")
            missing = [t for t in BUNDLED_TYPELIBS
                       if not os.path.exists(os.path.join(bundled, t))]
            if missing:
                print(f"rolodex selftest: FAIL — not bundled: {', '.join(missing)}")
                sys.exit(1)
        print("rolodex selftest: OK (GTK/Adw/cryptography loaded)")
        return
    app = RolodexApp()
    # Propagate the exit status: discarding it meant a GApplication startup failure still exited
    # 0, so a wrapper script or a CI step could not tell that the app never started.
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
