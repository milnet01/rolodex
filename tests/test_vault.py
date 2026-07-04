"""Regression + round-trip tests for Rolodex's GTK-free vault logic.

These cover the encryption layer and a couple of pure helpers. They are the seed of the
automated suite tracked as ROLO-0001; importing `rolodex` currently pulls in GTK because the
whole app is one file, so these run where GTK 4 is installed.

Run with: pytest tests/
"""

import os

import pytest
from cryptography.fernet import InvalidToken

import rolodex


PW = "correct horse battery staple"


def test_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "v.vault")
    vault, salt = rolodex.create_vault(PW, path)
    rolodex.add_entry(vault, "GitHub", [{"label": "Password", "value": "hunter2", "sensitive": True}])
    rolodex.save_vault(vault, PW, salt, path)

    loaded, loaded_salt = rolodex.load_vault(PW, path)
    assert loaded_salt == salt
    assert loaded["version"] == 2
    names = [e["name"] for e in loaded["entries"].values()]
    assert names == ["GitHub"]


def test_wrong_password_raises_invalid_token(tmp_path):
    path = str(tmp_path / "v.vault")
    rolodex.create_vault(PW, path)
    with pytest.raises(InvalidToken):
        rolodex.load_vault("wrong password", path)


def test_saved_vault_is_0600(tmp_path):
    path = str(tmp_path / "v.vault")
    rolodex.create_vault(PW, path)
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_migrate_vault_is_idempotent():
    v1 = {"entries": {"a": {"name": "X", "fields": [], "notes": ""}}}
    once = rolodex.migrate_vault(dict(v1, entries=dict(v1["entries"])))
    twice = rolodex.migrate_vault(once)
    assert twice["version"] == 2
    assert twice["categories"] == []
    assert twice["entries"]["a"]["category"] == ""
    # Running it again changes nothing.
    assert rolodex.migrate_vault(twice) == twice


def test_save_vault_write_error_surfaces_original_not_ebadf(tmp_path, monkeypatch):
    """Regression: the write-error path must not double-close the fd.

    The old code wrapped the fd in `with os.fdopen(...)` (which closes it) and *also* called
    `os.close(fd)` in an `except`, so a write failure raised OSError(EBADF) and masked the real
    error. This test forces `write` to fail and asserts the original exception propagates.
    """
    path = str(tmp_path / "v.vault")
    real_fdopen = os.fdopen

    class BoomFile:
        def __init__(self, fp):
            self._fp = fp

        def write(self, *_a):
            raise ValueError("disk full (simulated)")

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            self._fp.close()  # close the fd exactly once, like a real file object
            return False

    def fake_fdopen(fd, *a, **k):
        return BoomFile(real_fdopen(fd, *a, **k))

    monkeypatch.setattr(rolodex.os, "fdopen", fake_fdopen)

    with pytest.raises(ValueError, match="disk full"):
        rolodex.save_vault({"version": 2, "categories": [], "entries": {}}, PW, os.urandom(16), path)


def test_entries_noun_singular_plural():
    assert rolodex.entries_noun(0) == "entries"
    assert rolodex.entries_noun(1) == "entry"
    assert rolodex.entries_noun(2) == "entries"
