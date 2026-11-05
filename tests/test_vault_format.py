"""The VLT2 vault format, its Argon2id key and the VLT1 upgrade at unlock (ROLO-0005).

Each test names the invariant of docs/specs/vault-format-and-crypto.md it locks. A VLT1 file
is built here by hand, from hashlib's PBKDF2 and Fernet, because nothing in rolodex.py writes
one any more (INV-24).

Run with: pytest tests/
"""

import base64
import hashlib
import os
import struct

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

import rolodex


PW = "correct horse battery staple"
# Argon2id's smallest legal parameters: a VLT2 file written by a hypothetical older build, and
# cheap to derive.
LOW = (8, 1, 1)  # memory_cost KiB, iterations, lanes


def _write_vlt1(path, vault, password=PW, salt=None):
    salt = salt or os.urandom(16)
    raw = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000, dklen=32)
    token = Fernet(base64.urlsafe_b64encode(raw)).encrypt(
        rolodex.json.dumps(vault).encode())
    path.write_bytes(b"VLT1" + salt + token)
    return salt


def _write_vlt2(path, vault, params, password=PW, salt=None):
    salt = salt or os.urandom(16)
    memory, iterations, lanes = params
    raw = Argon2id(salt=salt, length=32, iterations=iterations, lanes=lanes,
                   memory_cost=memory).derive(password.encode())
    token = Fernet(base64.urlsafe_b64encode(raw)).encrypt(
        rolodex.json.dumps(vault).encode())
    path.write_bytes(b"VLT2" + struct.pack(">III", memory, iterations, lanes) + salt + token)
    return salt


def _header(memory, iterations, lanes, magic=b"VLT2"):
    return magic + struct.pack(">III", memory, iterations, lanes) + b"\x00" * 16 + b"token"


CURRENT = tuple(getattr(rolodex, name, None)
                for name in ("ARGON2_MEMORY_KIB", "ARGON2_ITERATIONS", "ARGON2_LANES"))
V1_DATA = {"entries": {"a": {"name": "Old", "fields": [], "notes": ""}}}  # pre-migration shape


# --- INV-20: the VLT2 layout --------------------------------------------------------------


def test_INV20_a_new_vault_is_vlt2_with_the_current_parameters(tmp_path):
    path = tmp_path / "v.vault"
    _vault, salt, _key = rolodex.create_vault_with_key(PW, str(path))
    data = path.read_bytes()
    assert data[:4] == b"VLT2"
    assert struct.unpack(">III", data[4:16]) == CURRENT
    assert data[16:32] == salt


# --- INV-21: a newer member of the family is "too new", anything else "not a vault" --------


def test_INV21_a_newer_magic_raises_vault_too_new(tmp_path):
    path = tmp_path / "v.vault"
    path.write_bytes(b"VLT9" + b"\x00" * 64)
    with pytest.raises(rolodex.VaultTooNewError) as exc:
        rolodex.load_vault_with_key(PW, str(path))
    assert isinstance(exc.value, ValueError)
    assert str(exc.value) == ("This vault was made by a newer version of Rolodex. "
                              "Update Rolodex to open it.")


@pytest.mark.parametrize("magic", [b"XXXX", b"VLT0", b"VLTx", b"vlt2"])
def test_INV21_other_bytes_are_not_a_vault(tmp_path, magic):
    path = tmp_path / "v.vault"
    path.write_bytes(magic + b"\x00" * 64)
    with pytest.raises(ValueError, match="Not a valid vault file") as exc:
        rolodex.load_vault_with_key(PW, str(path))
    assert not isinstance(exc.value, rolodex.VaultTooNewError)


# --- INV-22: the key, from the header's parameters ----------------------------------------


def test_INV22_derive_key_is_argon2id_at_the_current_parameters():
    salt = bytes(range(16))
    expected = base64.urlsafe_b64encode(Argon2id(
        salt=salt, length=32, iterations=3, lanes=4, memory_cost=262_144).derive(PW.encode()))
    assert CURRENT == (262_144, 3, 4)
    assert rolodex.derive_key(PW, salt) == expected


def test_INV22_a_vlt2_file_is_read_with_its_own_parameters(tmp_path):
    path = tmp_path / "v.vault"
    _write_vlt2(path, V1_DATA, LOW)
    vault, _salt, _key = rolodex.load_vault_with_key(PW, str(path))
    assert vault == V1_DATA


# --- INV-23: the header is checked before deriving ----------------------------------------


@pytest.mark.parametrize("data", [
    b"VLT2" + b"\x00" * 27,                 # one byte short of the 28 after the magic
    _header(8, 1, 0),                        # lanes zero
    _header(8, 0, 1),                        # iterations zero
    _header(15, 1, 2),                       # memory below 8 x lanes
], ids=["short", "lanes-0", "iterations-0", "memory-below-8-per-lane"])
def test_INV23_a_damaged_header_is_corrupt_not_a_wrong_password(tmp_path, monkeypatch, data):
    monkeypatch.setattr(rolodex, "derive_key", lambda *a: pytest.fail("derived"))
    path = tmp_path / "v.vault"
    path.write_bytes(data)
    with pytest.raises(ValueError, match="truncated or corrupt") as exc:
        rolodex.load_vault_with_key(PW, str(path))
    assert not isinstance(exc.value, rolodex.VaultTooNewError)


@pytest.mark.parametrize("data", [
    _header(0xFFFFFFFF, 3, 4),
    _header(4_194_305, 3, 4),
    _header(262_144, 65, 4),
    _header(262_144, 3, 65),
], ids=["memory-max-u32", "memory-cap+1", "iterations-cap+1", "lanes-cap+1"])
def test_INV23_a_parameter_above_its_cap_is_too_new_and_never_derived(tmp_path, monkeypatch,
                                                                        data):
    monkeypatch.setattr(rolodex, "derive_key", lambda *a: pytest.fail("derived"))
    path = tmp_path / "v.vault"
    path.write_bytes(data)
    with pytest.raises(rolodex.VaultTooNewError):
        rolodex.load_vault_with_key(PW, str(path))


def test_INV23_the_caps_are_the_spec_values():
    assert (rolodex.ARGON2_MAX_MEMORY_KIB, rolodex.ARGON2_MAX_ITERATIONS,
            rolodex.ARGON2_MAX_LANES) == (4_194_304, 64, 64)


# --- INV-24: a session always holds a current-format pair ---------------------------------


@pytest.mark.parametrize("writer", ["vlt1", "vlt2-low"])
def test_INV24_a_stale_file_returns_a_fresh_current_pair(tmp_path, writer):
    path = tmp_path / "v.vault"
    old_salt = (_write_vlt1(path, V1_DATA) if writer == "vlt1"
                else _write_vlt2(path, V1_DATA, LOW))
    vault, salt, key = rolodex.load_vault_with_key(PW, str(path))
    assert salt != old_salt and len(salt) == 16
    assert key == rolodex.derive_key(PW, salt)
    # The session's first save must write a vault the same password opens.
    rolodex.save_vault_with_key(vault, key, salt, str(path))
    assert path.read_bytes()[:4] == b"VLT2"
    assert rolodex.load_vault_with_key(PW, str(path))[0] == V1_DATA


def test_INV24_a_current_file_returns_its_own_salt_and_key(tmp_path):
    path = tmp_path / "v.vault"
    _vault, salt, key = rolodex.create_vault_with_key(PW, str(path))
    assert rolodex.load_vault_with_key(PW, str(path))[1:] == (salt, key)


# --- INV-25: unlocking upgrades the file --------------------------------------------------


def test_INV25_unlock_rewrites_a_vlt1_vault_as_vlt2_before_migrating(tmp_path):
    path = tmp_path / "v.vault"
    _write_vlt1(path, V1_DATA)
    vault, salt, key, _fingerprint = rolodex.unlock_vault(PW, str(path))
    assert vault == V1_DATA  # as decrypted: migrate_vault has not run
    assert path.read_bytes()[:4] == b"VLT2"
    assert path.read_bytes()[16:32] == salt
    assert rolodex.load_vault_with_key(PW, str(path)) == (V1_DATA, salt, key)


def test_INV25_unlock_leaves_a_current_vault_untouched(tmp_path):
    path = tmp_path / "v.vault"
    rolodex.create_vault_with_key(PW, str(path))
    before = rolodex.vault_fingerprint(str(path))
    rolodex.unlock_vault(PW, str(path))
    assert rolodex.vault_fingerprint(str(path)) == before


def test_INV25_a_failed_rewrite_still_unlocks(tmp_path, monkeypatch):
    path = tmp_path / "v.vault"
    _write_vlt1(path, V1_DATA)

    def refuse(*_a):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(rolodex, "write_private_file", refuse)
    vault, salt, key, _fingerprint = rolodex.unlock_vault(PW, str(path))
    assert vault == V1_DATA
    assert path.read_bytes()[:4] == b"VLT1"
    assert key == rolodex.derive_key(PW, salt)  # the first save writes VLT2


# --- ROLO-0098: the fingerprint is taken when unlock reads the file -----------------------


def _sync_lands_during_read(monkeypatch, path, new_bytes):
    """Make a sync client replace the vault file just after unlock has read it."""
    real_read = rolodex._read_vault

    def read_then_sync(password, p):
        result = real_read(password, p)
        other = path.with_name("synced.tmp")
        other.write_bytes(new_bytes)
        os.replace(other, path)
        return result

    monkeypatch.setattr(rolodex, "_read_vault", read_then_sync)


def test_ROLO0098_unlock_returns_the_fingerprint_it_read_not_a_later_one(tmp_path, monkeypatch):
    path = tmp_path / "v.vault"
    rolodex.create_vault_with_key(PW, str(path))
    before = rolodex.vault_fingerprint(str(path))
    _sync_lands_during_read(monkeypatch, path, b"a synced change")
    *_unlocked, fingerprint = rolodex.unlock_vault(PW, str(path))
    assert fingerprint == before
    # So the session's first save sees the change instead of adopting it as its baseline.
    assert rolodex.vault_fingerprint(str(path)) != fingerprint


def test_ROLO0098_the_upgrade_at_unlock_skips_a_file_that_changed_meanwhile(tmp_path,
                                                                             monkeypatch):
    path = tmp_path / "v.vault"
    _write_vlt1(path, V1_DATA)
    _sync_lands_during_read(monkeypatch, path, b"a synced change")
    vault, _salt, _key, _fingerprint = rolodex.unlock_vault(PW, str(path))
    assert vault == V1_DATA
    assert path.read_bytes() == b"a synced change"


def test_ROLO0098_after_the_upgrade_the_fingerprint_is_the_rewritten_file(tmp_path):
    path = tmp_path / "v.vault"
    _write_vlt1(path, V1_DATA)
    *_unlocked, fingerprint = rolodex.unlock_vault(PW, str(path))
    assert path.read_bytes()[:4] == b"VLT2"
    assert fingerprint == rolodex.vault_fingerprint(str(path))


# --- INV-26: adopt checks the header and copies verbatim ----------------------------------


@pytest.mark.parametrize("writer", ["vlt1", "vlt2-low"])
def test_INV26_adopt_copies_either_format_verbatim(tmp_path, writer):
    backup = tmp_path / "backup.vault"
    (_write_vlt1(backup, V1_DATA) if writer == "vlt1" else _write_vlt2(backup, V1_DATA, LOW))
    live = tmp_path / "contacts.vault"
    rolodex.adopt_vault_file(str(backup), str(live))
    assert live.read_bytes() == backup.read_bytes()


def test_INV26_adopt_reports_a_newer_vault_as_too_new(tmp_path):
    backup = tmp_path / "backup.vault"
    backup.write_bytes(b"VLT9" + b"\x00" * 64)
    live = tmp_path / "contacts.vault"
    live.write_bytes(b"keep me")
    with pytest.raises(rolodex.VaultTooNewError):
        rolodex.adopt_vault_file(str(backup), str(live))
    assert live.read_bytes() == b"keep me"


def test_INV26_adopt_refuses_a_damaged_vlt2_header(tmp_path):
    backup = tmp_path / "backup.vault"
    backup.write_bytes(_header(8, 1, 0))
    live = tmp_path / "contacts.vault"
    with pytest.raises(ValueError, match="not a Rolodex vault"):
        rolodex.adopt_vault_file(str(backup), str(live))
    assert not live.exists()

