"""Regression tests for a batch of pure-layer fixes.

Each test locks one fix that has already landed and names the failure mode it closes off, in
its own docstring or a leading comment. These are GTK-free, like the rest of the pure layer;
none of them opens a window or a GTK main loop. Run with: pytest tests/
"""

import base64
import json
import os
import subprocess

import pytest

import rolodex


PW = "correct horse battery staple"


# --- load_vault: a truncated salt must fail as "corrupt", not "wrong password" ------------


def test_load_vault_rejects_truncated_salt(tmp_path):
    """A short salt used to be silently accepted by derive_key, so a truncated vault -- a
    partial copy, an interrupted sync -- failed decryption with InvalidToken, which the unlock
    dialog renders as "Wrong password." That invites deleting a file a backup could still have
    salvaged. This must raise ValueError, not InvalidToken.
    """
    path = tmp_path / "trunc.vault"
    path.write_bytes(rolodex.MAGIC + b"\x00" * 5)  # salt must be exactly 16 bytes
    with pytest.raises(ValueError, match="truncated"):
        rolodex.load_vault(PW, str(path))


# --- migrate_vault: refuse a future version and non-vault shapes, stay idempotent ----------


def test_migrate_vault_rejects_future_version():
    vault = {"version": 3, "entries": {}}
    with pytest.raises(ValueError, match="newer version"):
        rolodex.migrate_vault(vault)


def test_migrate_vault_rejects_non_dict_and_missing_entries():
    with pytest.raises(ValueError):
        rolodex.migrate_vault(["not", "a", "dict"])
    with pytest.raises(ValueError):
        rolodex.migrate_vault({"version": 2})  # no "entries" key at all
    with pytest.raises(ValueError):
        rolodex.migrate_vault({"version": 2, "entries": "not a dict"})


def test_migrate_vault_still_idempotent_on_well_formed_v1_and_v2():
    # The version/shape guard must not have broken the case it was added around.
    v1 = {"entries": {"a": {"name": "X", "fields": [], "notes": ""}}}
    once = rolodex.migrate_vault(dict(v1, entries=dict(v1["entries"])))
    assert once["version"] == 2
    twice = rolodex.migrate_vault(once)
    assert twice == once

    v2 = {"version": 2, "categories": ["Work"], "entries": {}}
    assert rolodex.migrate_vault(dict(v2)) == v2


# --- load_config: {} for anything that parses but isn't a JSON object ----------------------


@pytest.mark.parametrize("payload", ["null", "[]", "5", '"hello"'])
def test_load_config_returns_empty_dict_for_non_object_json(tmp_path, payload):
    """Valid non-object JSON used to satisfy json.load and then raise AttributeError out of
    MainWindow.__init__ -- the app failed to open its window at all, with an unhandled
    traceback rather than a message. The README documents this file as hand-editable.
    """
    path = tmp_path / ".conf"
    path.write_text(payload)
    assert rolodex.load_config(str(path)) == {}


def test_load_config_returns_the_dict_for_a_real_object(tmp_path):
    path = tmp_path / ".conf"
    path.write_text('{"a": 1}')
    assert rolodex.load_config(str(path)) == {"a": 1}


# --- config_int: fall back rather than raise ------------------------------------------------


def test_config_int_falls_back_on_bad_or_missing_values():
    """int("five") used to raise from inside a GLib.idle_add callback after the unlock dialog
    had already disabled its button -- stuck on "Unlocking..." forever with the vault already
    decrypted in memory and nothing on screen to say why.
    """
    assert rolodex.config_int({"x": "five"}, "x", 42) == 42
    assert rolodex.config_int({"x": None}, "x", 42) == 42
    assert rolodex.config_int({}, "x", 42) == 42
    # And the good paths still work.
    assert rolodex.config_int({"x": "7"}, "x", 42) == 7
    assert rolodex.config_int({"x": 9}, "x", 42) == 9


# --- save_config: atomic write, no lost keys, no leftover temp -----------------------------


def test_save_config_is_atomic_and_preserves_other_keys(tmp_path):
    """A plain truncate-then-write left a partial file on a kill or ENOSPC between truncate
    and flush, which the next load_config read as {} -- silently resetting window geometry,
    both security timeouts, skipped_update_version and the check_for_updates opt-in.
    """
    path = str(tmp_path / ".conf")
    rolodex.save_config({"a": 1, "b": 2}, path)
    rolodex.save_config({"b": 3}, path)

    with open(path) as f:
        assert json.load(f) == {"a": 1, "b": 3}
    # No ".tmp" sibling left behind by the atomic rename.
    leftovers = [p for p in os.listdir(tmp_path) if p != os.path.basename(path)]
    assert leftovers == []


# --- parse_text_file: an empty or blank-only file yields no entries ------------------------


def test_parse_text_file_returns_empty_list_for_empty_and_blank_files(tmp_path):
    """str.split never returns [], so an empty or whitespace-only file used to produce one
    block of [""] -- one entry with an empty name -- which defeated the caller's `if not
    parsed` check, so "No entries found in file." was unreachable and importing silently wrote
    a nameless entry the editor forbids.
    """
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    assert rolodex.parse_text_file(str(empty)) == []

    blank = tmp_path / "blank.txt"
    blank.write_text("\n\n   \n\t\n")
    assert rolodex.parse_text_file(str(blank)) == []

    # The guard must not have eaten real blocks.
    normal = tmp_path / "normal.txt"
    normal.write_text("A\nUser: x\n\nB\nUser: y\n")
    entries = rolodex.parse_text_file(str(normal))
    assert [e["name"] for e in entries] == ["A", "B"]


# --- copy_to_clipboard: fall through past a present-but-failing tool ------------------------


def test_copy_to_clipboard_falls_through_to_the_next_available_tool(monkeypatch):
    """wl-clipboard merely being INSTALLED under an X11 session -- which several distros
    arrange by default -- made every copy fail, because wl-copy exits non-zero with no
    Wayland display while a working xclip sat untried on the next line. Only "available"
    (shutil.which) tools should be tried at all.
    """
    invoked = []

    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if cmd in ("wl-copy", "xclip") else None

    def fake_run(cmd, **kwargs):
        invoked.append(cmd[0])
        returncode = 1 if cmd[0] == "wl-copy" else 0
        return subprocess.CompletedProcess(cmd, returncode)

    monkeypatch.setattr(rolodex.shutil, "which", fake_which)
    monkeypatch.setattr(rolodex.subprocess, "run", fake_run)

    assert rolodex.copy_to_clipboard("secret") is True
    assert invoked == ["wl-copy", "xclip"], "xclip must actually have been invoked after wl-copy failed"


def test_copy_to_clipboard_returns_false_only_when_every_available_tool_fails(monkeypatch):
    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if cmd in ("wl-copy", "xclip") else None

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(rolodex.shutil, "which", fake_which)
    monkeypatch.setattr(rolodex.subprocess, "run", fake_run)

    assert rolodex.copy_to_clipboard("secret") is False


# --- field_is_sensitive: a recognised TOTP seed masks even when sensitive=False ------------


def test_field_is_sensitive_masks_a_totp_seed_regardless_of_the_stored_flag():
    """SENSITIVE_KEYWORDS and TOTP_LABEL_KEYWORDS only overlap on "authenticator", so a field
    labelled "2FA", "TOTP" or "OTP" was stored non-sensitive and then rendered in permanent
    cleartext beside the live code derived from it. An otpauth:// URI under a totally
    non-sensitive label must be caught too -- no keyword list could catch its secret= value.
    """
    seed = base64.b32encode(b"12345678901234567890").decode()

    bare_2fa = {"label": "2FA", "value": seed, "sensitive": False}
    assert rolodex.field_is_sensitive(bare_2fa) is True

    uri_under_notes = {
        "label": "Notes",
        "value": f"otpauth://totp/GitHub?secret={seed}",
        "sensitive": False,
    }
    assert rolodex.field_is_sensitive(uri_under_notes) is True

    # And it has not become always-true.
    ordinary = {"label": "Username", "value": "octocat", "sensitive": False}
    assert rolodex.field_is_sensitive(ordinary) is False


# --- generate_password: small lengths are not biased to declaration order ------------------


def test_generate_password_short_length_is_not_biased_to_declaration_order():
    """The per-class draws were sliced to `length` in the fixed lower/upper/digits/symbols
    order BEFORE shuffling, so generate_password(length=2) could only ever return a lowercase
    and an uppercase character -- digits and symbols were structurally unreachable.

    At length=2 with all four classes enabled, each draw keeps a random 2-of-4 selection of
    classes (post-shuffle), so any one class is absent from a given draw with probability
    C(3,2)/C(4,2) = 0.5. Across 300 independent draws the chance a genuinely-reachable class
    never appears is 0.5**300 -- indistinguishable from zero -- so this will not flake under
    the fix and would have failed every single run under the bug (digits/symbols always 0/300).
    """
    classes_seen = set()
    for _ in range(300):
        pw = rolodex.generate_password(length=2, lower=True, upper=True, digits=True, symbols=True)
        for c in pw:
            if c.islower():
                classes_seen.add("lower")
            elif c.isupper():
                classes_seen.add("upper")
            elif c.isdigit():
                classes_seen.add("digits")
            elif c in rolodex.PW_GEN_SYMBOLS:
                classes_seen.add("symbols")
    assert classes_seen == {"lower", "upper", "digits", "symbols"}, (
        f"only saw {classes_seen} across 300 draws of length=2 -- a class is unreachable"
    )


# --- _decode_base32: reject characters that fold INTO the alphabet via str.upper() ---------


def test_decode_base32_rejects_characters_that_fold_into_the_alphabet_via_upper():
    """str.upper() applies full Unicode case mapping, so a character that is not base32 can
    still fold into it: dotless i 'ı' -> 'I', long s 'ſ' -> 'S'. Checked after upper() rather
    than rejected outright, these would decode SILENTLY to a WRONG secret instead of failing.
    """
    assert rolodex._decode_base32("ı" * 16) is None
    assert rolodex._decode_base32("ſ" * 16) is None

    # A genuine seed must still decode.
    valid = base64.b32encode(b"12345678901234567890").decode()
    assert rolodex._decode_base32(valid) is not None


# --- _parse_otpauth_uri: digits and period bounds -------------------------------------------

_VALID_SEED = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize("digits,expected", [(5, None), (6, 6), (7, 7), (8, 8), (9, None)])
def test_parse_otpauth_uri_enforces_digits_bounds(digits, expected):
    uri = f"otpauth://totp/x?secret={_VALID_SEED}&digits={digits}"
    result = rolodex._parse_otpauth_uri(uri)
    if expected is None:
        assert result is None
    else:
        assert result["digits"] == expected


@pytest.mark.parametrize("period,expected", [(0, None), (1, 1), (30, 30), (300, 300), (301, None)])
def test_parse_otpauth_uri_enforces_period_bounds(period, expected):
    uri = f"otpauth://totp/x?secret={_VALID_SEED}&period={period}"
    result = rolodex._parse_otpauth_uri(uri)
    if expected is None:
        assert result is None
    else:
        assert result["period"] == expected


def test_parse_otpauth_uri_survives_a_malformed_authority():
    """urlsplit raises ValueError on an unbalanced or invalid bracketed host ("otpauth://[totp"
    has an opening '[' with no matching ']'). This runs per field from the detail-view render,
    so an exception here strands the whole entry behind an undrawable pane -- and
    parse_totp_field's docstring promises it never raises on user data.
    """
    assert rolodex._parse_otpauth_uri("otpauth://[totp") is None


# --- rename_category: refuse a collision, still rename cleanly -----------------------------


def test_rename_category_rejects_collision_with_an_existing_name():
    """Renaming onto an existing name would leave two identical entries in the ordered
    categories list, which the sidebar renders twice and delete_category half-removes.
    """
    vault = {"version": 2, "categories": ["Work", "Personal"], "entries": {}}
    with pytest.raises(ValueError, match="already exists"):
        rolodex.rename_category(vault, "Work", "Personal")
    # A rejected rename must not have mutated the list.
    assert vault["categories"] == ["Work", "Personal"]


def test_rename_category_normal_rename_preserves_position_and_rewrites_members():
    vault = {
        "version": 2,
        "categories": ["A", "Work", "B"],
        "entries": {
            "1": {"name": "X", "category": "Work"},
            "2": {"name": "Y", "category": "A"},
        },
    }
    rolodex.rename_category(vault, "Work", "Job")
    assert vault["categories"] == ["A", "Job", "B"]  # position preserved, not appended
    assert vault["entries"]["1"]["category"] == "Job"
    assert vault["entries"]["2"]["category"] == "A"  # unrelated entry untouched


# --- download_and_verify: an arbitrary exception becomes UpdateError -----------------------


def test_download_and_verify_converts_a_non_update_error_to_update_error(tmp_path):
    """download_and_verify's only caller catches UpdateError and UpdateVerificationError and
    nothing else, so an OSError (mkstemp on a read-only/full directory, or a network failure
    re-raised verbatim by download_to) escaping as its own type killed the worker thread with
    a stderr traceback and left the "Downloading..." toast simply hanging.

    A genuine signature failure raising UpdateVerificationError (the sibling this must NOT
    become) is already covered by test_update.py's
    test_INV8_a_one_byte_tamper_raises_and_leaves_no_temp, so it is not duplicated here.
    """
    target = str(tmp_path / "rolodex-linux-x86_64")

    def boom(url, dest, max_bytes):
        raise OSError("network unreachable")

    info = rolodex.UpdateInfo("9.9.9", "https://e.invalid/a", "https://e.invalid/a.sig", "")
    with pytest.raises(rolodex.UpdateError) as excinfo:
        rolodex.download_and_verify(info, downloader=boom, target=target)

    assert type(excinfo.value) is rolodex.UpdateError, (
        "an OSError must become the plain UpdateError, not its UpdateVerificationError subclass"
    )
    assert isinstance(excinfo.value.__cause__, OSError)


# --- ROLO-0043: saving must not re-run the KDF ---------------------------------------------


def _counting_derive_key(monkeypatch):
    """Wrap rolodex.derive_key with a call counter, returning the counter list."""
    calls = []
    real = rolodex.derive_key

    def counted(password, salt):
        calls.append((password, salt))
        return real(password, salt)

    monkeypatch.setattr(rolodex, "derive_key", counted)
    return calls


def test_ROLO0043_save_vault_with_key_never_derives(tmp_path, monkeypatch):
    """save_vault_with_key must do zero KDF work.

    _save() calls it from a GTK signal handler on every mutation -- add, edit, delete, field
    reorder, drag-to-category. Deriving there put ITERATIONS rounds of PBKDF2 on the UI thread
    for each one, which is the freeze this function exists to remove. If a later refactor makes
    it derive again the app still works and the freeze silently returns, so the absence of the
    call is the thing worth asserting.
    """
    path = str(tmp_path / "v.vault")
    vault, salt, key = rolodex.create_vault_with_key(PW, path)

    calls = _counting_derive_key(monkeypatch)
    rolodex.save_vault_with_key(vault, key, salt, path)

    assert calls == [], f"save_vault_with_key ran the KDF {len(calls)} time(s); it must run none"


def test_ROLO0043_unlock_and_create_derive_exactly_once(tmp_path, monkeypatch):
    """The two session-opening paths must each derive once, not twice.

    The point of returning the key is that the caller keeps it. A wrapper that derived, then
    let its caller derive again, would pass a round-trip test while costing two KDF runs per
    unlock -- doubling the one wait the user cannot avoid.
    """
    path = str(tmp_path / "v.vault")

    calls = _counting_derive_key(monkeypatch)
    rolodex.create_vault_with_key(PW, path)
    assert len(calls) == 1, f"create_vault_with_key derived {len(calls)} times, expected 1"

    calls.clear()
    rolodex.load_vault_with_key(PW, path)
    assert len(calls) == 1, f"load_vault_with_key derived {len(calls)} times, expected 1"


def test_ROLO0043_returned_key_round_trips_through_the_password(tmp_path):
    """A vault written under the returned key must still open with the master password.

    The key and salt are a pair. Writing under a key derived from a different salt produces a
    file no password opens, and the vault is the user's only copy -- so this checks the key
    handed back really is the one the stored salt implies.
    """
    path = str(tmp_path / "v.vault")
    vault, salt, key = rolodex.create_vault_with_key(PW, path)
    vault["entries"]["id-1"] = {"name": "Bank", "category": "", "fields": [], "notes": ""}

    rolodex.save_vault_with_key(vault, key, salt, path)

    reloaded, reloaded_salt = rolodex.load_vault(PW, path)
    assert reloaded["entries"]["id-1"]["name"] == "Bank"
    assert reloaded_salt == salt


def test_ROLO0043_legacy_wrappers_keep_their_signatures(tmp_path):
    """save_vault / load_vault / create_vault must keep their exact pre-ROLO-0043 shape.

    They are the documented pure-layer contract and the tests, the spec and any future caller
    bind to them. The _with_key siblings were added beside them precisely so this did not
    change; a wrapper that leaked the third tuple member would break every existing caller.
    """
    path = str(tmp_path / "v.vault")

    vault, salt = rolodex.create_vault(PW, path)
    assert isinstance(salt, bytes) and len(salt) == 16

    rolodex.save_vault(vault, PW, salt, path)

    loaded, loaded_salt = rolodex.load_vault(PW, path)
    assert loaded == vault
    assert loaded_salt == salt


# --- ROLO-0066: password reuse is counted across entries, not fields -----------------------


def _reuse_vault():
    return {
        "version": 2,
        "categories": [],
        "entries": {
            "e1": {"name": "Bank", "category": "", "notes": "", "fields": [
                {"label": "Password", "value": "same-secret-value", "sensitive": True},
                {"label": "Backup password", "value": "same-secret-value", "sensitive": True},
            ]},
            "e2": {"name": "Mail", "category": "", "notes": "", "fields": [
                {"label": "Password", "value": "a-different-secret", "sensitive": True},
            ]},
        },
    }


def test_ROLO0066_two_fields_in_one_entry_are_not_reuse():
    """One account holding the same secret twice is not password reuse.

    Reuse warns that a single secret protects two different accounts. Counting per FIELD flagged
    an entry whose "Password" and "Backup password" hold one value -- which is one account, and
    a warning that fires there teaches the user to ignore the warning everywhere.
    """
    findings = {(f["entry_name"], f["label"]): f for f in rolodex.audit_passwords(_reuse_vault())}

    for label in ("Password", "Backup password"):
        f = findings[("Bank", label)]
        assert not f["reused"], f"Bank/{label} flagged as reused; both fields are one account"
        assert f["reuse_count"] == 1, f"Bank/{label} reuse_count {f['reuse_count']}, expected 1"


def test_ROLO0066_same_secret_in_two_entries_is_reuse():
    """The case the check exists for must still fire."""
    vault = _reuse_vault()
    vault["entries"]["e2"]["fields"][0]["value"] = "same-secret-value"

    findings = {(f["entry_name"], f["label"]): f for f in rolodex.audit_passwords(vault)}

    assert findings[("Mail", "Password")]["reused"]
    assert findings[("Bank", "Password")]["reused"]
    # Two entries share it, not three fields -- reuse_count counts accounts.
    assert findings[("Bank", "Password")]["reuse_count"] == 2, (
        "reuse_count must count distinct entries, not sensitive fields"
    )


# --- ROLO-0079: the master-password minimum is a floor -------------------------------------


def test_ROLO0079_min_password_length_is_a_floor_of_12():
    """MIN_PASSWORD_LENGTH may be raised, never lowered below 12.

    Same shape as the KDF iteration floor: an 8-character master password is weak against the
    offline guessing SECURITY.md names as the main threat, and lowering the bound is a silent
    security regression that no other test would notice. Raising it is fine and keeps this green.
    """
    assert rolodex.MIN_PASSWORD_LENGTH >= 12, (
        f"MIN_PASSWORD_LENGTH is {rolodex.MIN_PASSWORD_LENGTH}; 12 is the floor (ROLO-0079)"
    )
