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


# --- Password generator (ROLO-0004) -------------------------------------------------------


def test_generate_password_length_and_default_classes():
    pw = rolodex.generate_password(length=24)
    assert len(pw) == 24
    # Default enables every class; each guaranteed at least once at this length.
    assert any(c.islower() for c in pw)
    assert any(c.isupper() for c in pw)
    assert any(c.isdigit() for c in pw)
    assert any(c in rolodex.PW_GEN_SYMBOLS for c in pw)


def test_generate_password_respects_disabled_classes():
    pw = rolodex.generate_password(length=40, upper=False, digits=False, symbols=False)
    assert pw and all(c.islower() for c in pw)


def test_generate_password_guarantees_each_selected_class():
    # length == number of selected classes → exactly one of each, no room for filler.
    pw = rolodex.generate_password(length=2, lower=True, upper=False, digits=True, symbols=False)
    assert len(pw) == 2
    assert any(c.islower() for c in pw) and any(c.isdigit() for c in pw)


def test_generate_password_requires_a_class():
    with pytest.raises(ValueError):
        rolodex.generate_password(lower=False, upper=False, digits=False, symbols=False)


def test_generate_password_rejects_zero_length():
    with pytest.raises(ValueError):
        rolodex.generate_password(length=0)


def test_generate_password_is_random():
    # Two 32-char draws colliding by chance is astronomically unlikely; a match means a bug.
    assert rolodex.generate_password(length=32) != rolodex.generate_password(length=32)


# --- Import parser (ROLO-0001 scope) ------------------------------------------------------


def test_parse_text_file(tmp_path):
    path = tmp_path / "import.txt"
    path.write_text(
        "GitHub:\n"
        "Username: octocat\n"
        "Password: hunter2\n"
        "a free-form note\n"
        "\n"
        "Email\n"
        "Address: me@example.com\n",
        encoding="utf-8",
    )
    entries = rolodex.parse_text_file(str(path))
    assert [e["name"] for e in entries] == ["GitHub", "Email"]

    gh = entries[0]
    assert gh["fields"] == [
        {"label": "Username", "value": "octocat", "sensitive": False},
        {"label": "Password", "value": "hunter2", "sensitive": True},
    ]
    assert gh["notes"] == "a free-form note"
    assert entries[1]["fields"][0]["label"] == "Address"


# --- Search (ROLO-0001 scope) -------------------------------------------------------------


def _vault_with(entries):
    vault = {"version": 2, "categories": [], "entries": {}}
    for name, fields, notes, category in entries:
        rolodex.add_entry(vault, name, fields, notes, category)
    return vault


def test_search_entries_matches_name_field_category_notes():
    vault = _vault_with([
        ("GitHub", [{"label": "Password", "value": "hunter2", "sensitive": True}], "", ""),
        ("GitLab", [], "", "Work"),
        ("Bank", [], "savings account", ""),
    ])
    # Name matches, returned sorted by name.
    names = [e["name"] for _eid, e in rolodex.search_entries(vault, "git")]
    assert names == ["GitHub", "GitLab"]
    # Field value, category, and notes each match.
    assert [e["name"] for _e, e in rolodex.search_entries(vault, "hunter")] == ["GitHub"]
    assert [e["name"] for _e, e in rolodex.search_entries(vault, "work")] == ["GitLab"]
    assert [e["name"] for _e, e in rolodex.search_entries(vault, "savings")] == ["Bank"]
    assert rolodex.search_entries(vault, "no-such-thing") == []


# --- Duplicate-name detection (ROLO-0023) -------------------------------------------------


def test_find_entry_by_name_is_case_and_whitespace_insensitive():
    vault = _vault_with([("GitHub", [], "", "")])
    (eid,) = vault["entries"].keys()
    assert rolodex.find_entry_by_name(vault, "github") == eid
    assert rolodex.find_entry_by_name(vault, "  GITHUB  ") == eid
    assert rolodex.find_entry_by_name(vault, "GitLab") is None


def test_find_entry_by_name_excludes_self():
    vault = _vault_with([("GitHub", [], "", "")])
    (eid,) = vault["entries"].keys()
    # Editing the same entry must not flag it as a duplicate of itself.
    assert rolodex.find_entry_by_name(vault, "GitHub", exclude_id=eid) is None
    # But a *different* entry with the same name still counts.
    other = rolodex.add_entry(vault, "GitHub", [])
    assert rolodex.find_entry_by_name(vault, "GitHub", exclude_id=other) == eid


# --- Category helpers (ROLO-0001 scope) ---------------------------------------------------


def test_add_category_rejects_duplicates():
    vault = _vault_with([])
    assert rolodex.add_category(vault, "Work") is True
    assert rolodex.add_category(vault, "Work") is False
    assert vault["categories"] == ["Work"]


def test_rename_category_updates_member_entries():
    vault = _vault_with([("Job login", [], "", "Work")])
    rolodex.add_category(vault, "Work")
    rolodex.rename_category(vault, "Work", "Job")
    assert vault["categories"] == ["Job"]
    assert next(iter(vault["entries"].values()))["category"] == "Job"


def test_delete_category_orphans_member_entries():
    vault = _vault_with([("Thing", [], "", "Work")])
    rolodex.add_category(vault, "Work")
    rolodex.delete_category(vault, "Work")
    assert vault["categories"] == []
    assert next(iter(vault["entries"].values()))["category"] == ""


def test_entries_by_category_groups_and_treats_orphans_as_uncategorised():
    vault = _vault_with([
        ("A-entry", [], "", "Real"),
        ("B-entry", [], "", ""),
        ("C-entry", [], "", "Ghost"),  # category not in categories list
    ])
    rolodex.add_category(vault, "Real")
    groups = rolodex.entries_by_category(vault)
    assert [e["name"] for _eid, e in groups["Real"]] == ["A-entry"]
    # Uncategorised and orphaned-category entries both land under "".
    assert sorted(e["name"] for _eid, e in groups[""]) == ["B-entry", "C-entry"]


# --- Password health (ROLO-0008) ---

def test_password_strength_tiers():
    assert rolodex.password_strength("") == 0             # empty
    assert rolodex.password_strength("abc") == 1          # too short
    assert rolodex.password_strength("abcdefghij") == 1   # long but single class
    assert rolodex.password_strength("aB3!") == 1         # all classes but too short
    assert rolodex.password_strength("abcdefgh12") == 2   # 10 chars, 2 classes
    assert rolodex.password_strength("Abcdefgh1234") == 3  # 12 chars, 3 classes
    assert rolodex.password_strength("Abcdefgh1234!xyz") == 4  # 16 chars, 4 classes


def _sensitive(label, value):
    return {"label": label, "value": value, "sensitive": True}


def test_audit_passwords_flags_weak_and_orders_worst_first():
    vault = _vault_with([
        ("Strong", [_sensitive("Password", "Abcdefgh1234!xyz")], "", ""),
        ("Weak", [_sensitive("Password", "abc")], "", ""),
    ])
    findings = rolodex.audit_passwords(vault)
    assert [f["entry_name"] for f in findings] == ["Weak", "Strong"]
    assert findings[0]["strength_label"] == "Weak"
    assert findings[1]["strength_label"] == "Strong"


def test_audit_passwords_detects_reuse_across_entries():
    vault = _vault_with([
        ("GitHub", [_sensitive("Password", "shared-secret-123")], "", ""),
        ("GitLab", [_sensitive("Password", "shared-secret-123")], "", ""),
        ("Unique", [_sensitive("Password", "a-different-secret")], "", ""),
    ])
    findings = {f["entry_name"]: f for f in rolodex.audit_passwords(vault)}
    assert findings["GitHub"]["reused"] and findings["GitHub"]["reuse_count"] == 2
    assert findings["GitLab"]["reused"] and findings["GitLab"]["reuse_count"] == 2
    assert not findings["Unique"]["reused"] and findings["Unique"]["reuse_count"] == 1


def test_audit_passwords_ignores_non_sensitive_and_empty_fields():
    vault = _vault_with([
        ("A", [{"label": "Username", "value": "same", "sensitive": False},
               {"label": "Password", "value": "", "sensitive": True}], "", ""),
        ("B", [{"label": "Username", "value": "same", "sensitive": False}], "", ""),
    ])
    # Non-sensitive duplicate usernames and the empty password produce no findings.
    assert rolodex.audit_passwords(vault) == []
