"""CSV import and export (ROLO-0012), the pure layer.

Each test is named for the invariant of docs/specs/ROLO-0012-csv-import-export.md it locks.
Fixtures are literal CSV text written to tmp_path. Run with: pytest tests/
"""

import pytest

import rolodex


def _write(tmp_path, text, name="in.csv"):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def _field(label, value, sensitive):
    return {"label": label, "value": value, "sensitive": sensitive}


def _import(tmp_path, text):
    """File text -> parsed entries, through the same three steps the menu runs."""
    header, rows = rolodex.read_csv_file(_write(tmp_path, text))
    fmt = rolodex.detect_csv_format(header)
    assert fmt is not None, header
    return fmt, rolodex.csv_rows_to_entries(fmt, header, rows)


def _comparable(vault):
    return sorted(
        (e["name"], rolodex.entry_category(vault, e), e["notes"], e["created"], e["modified"],
         [(f["label"], f["value"], f["sensitive"]) for f in e["fields"]])
        for e in vault["entries"].values()
    )


# --- INV-1 -------------------------------------------------------------------------------


def test_INV1_rolodex_csv_round_trips(tmp_path):
    src = {"version": 2, "categories": ["Work", "Games"], "entries": {}}
    rolodex.add_entry(src, "Mail", [
        _field("Username", "bob", False),
        _field("Recovery phrase", " lead, with \"quotes\"", True),  # keyword-free, hidden
        _field("Password", "=SUM(A1)", False),                       # keyword label, shown
    ], notes="line one\r\nline two\nline three", category="Work")
    rolodex.add_entry(src, "Café ☕", [], category="Games")
    rolodex.add_entry(src, "Plain", [_field("URL", "https://x.test", False)])
    # Timestamps that a fresh stamp could never reproduce.
    for i, entry in enumerate(src["entries"].values()):
        entry["created"] = f"2020-01-0{i + 1}T10:00:00+02:00"
        entry["modified"] = f"2021-01-0{i + 1}T10:00:00"

    path = tmp_path / "out.csv"
    path.write_bytes(rolodex.export_csv(src).encode("utf-8"))
    header, rows = rolodex.read_csv_file(str(path))
    assert rolodex.detect_csv_format(header) == "rolodex"
    parsed = rolodex.csv_rows_to_entries("rolodex", header, rows)

    dst = {"version": 2, "categories": [], "entries": {}}
    rolodex.import_entries(dst, parsed, category=None)
    assert _comparable(dst) == _comparable(src)


# --- INV-2 / INV-3 -----------------------------------------------------------------------

BITWARDEN_OLD = ("folder,favorite,type,name,notes,fields,reprompt,login_uri,login_username,"
                 "login_password,login_totp")
BITWARDEN = BITWARDEN_OLD + ",archivedDate"
BITWARDEN_ORG = ("collections,type,name,notes,fields,reprompt,login_uri,login_username,"
                 "login_password,login_totp")
KEEPASSXC_OLD = '"Group","Title","Username","Password","URL","Notes"'
KEEPASSXC = KEEPASSXC_OLD + ',"TOTP","Icon","Last Modified","Created"'
CHROMIUM_OLD = "name,url,username,password"
CHROMIUM = CHROMIUM_OLD + ",note"
FIREFOX = ('"url","username","password","httpRealm","formActionOrigin","guid","timeCreated",'
           '"timeLastUsed","timePasswordChanged"')
ONEPASSWORD = "Title,Url,Username,Password,OTPAuth,Favorite,Archived,Tags,Notes"
ROLODEX = "name,category,notes,created,modified,field_1_label,field_1_value,field_1_hidden"


@pytest.mark.parametrize("line, fmt", [
    (BITWARDEN, "bitwarden"), (BITWARDEN_OLD, "bitwarden"), (BITWARDEN_ORG, "bitwarden"),
    (KEEPASSXC, "keepassxc"), (KEEPASSXC_OLD, "keepassxc"),
    (CHROMIUM, "chromium"), (CHROMIUM_OLD, "chromium"),
    (FIREFOX, "firefox"), (ONEPASSWORD, "1password"),
    (ROLODEX, "rolodex"), ("name,category,notes,created,modified", "rolodex"),
])
def test_INV2_detects_each_writer_header(tmp_path, line, fmt):
    header, _ = rolodex.read_csv_file(_write(tmp_path, line + "\r\n"))
    assert rolodex.detect_csv_format(header) == fmt
    shouted = [f"  {cell.upper()} " for cell in header]
    assert rolodex.detect_csv_format(shouted) == fmt


def test_INV3_ambiguous_or_unknown_header_is_none():
    both = ["group", "title", "username", "password", "url", "notes", "otpauth"]
    assert rolodex.detect_csv_format(both) is None
    assert rolodex.detect_csv_format(["site", "login", "secret"]) is None
    # A rolodex header whose field groups skip a number is not the rolodex structure.
    assert rolodex.detect_csv_format(
        ["name", "category", "notes", "created", "modified",
         "field_2_label", "field_2_value", "field_2_hidden"]) is None


# --- INV-4 / INV-5 / INV-6 ---------------------------------------------------------------


def _fields(entry):
    return [(f["label"], f["value"], f["sensitive"]) for f in entry["fields"]]


def test_INV4_bitwarden_maps_columns(tmp_path):
    fmt, [e] = _import(tmp_path, BITWARDEN + "\r\n"
                       'Work,1,login,GitHub,a note,"PIN: 1234\nloose line\nColour: blue",0,'
                       "https://github.com,bob,hunter2,JBSWY3DPEHPK3PXP,\r\n")
    assert fmt == "bitwarden"
    assert e["name"] == "GitHub" and e["category"] == "Work"
    assert e["notes"] == "a note\nloose line"
    assert _fields(e) == [
        ("PIN", "1234", True), ("Colour", "blue", False),
        ("URL", "https://github.com", False), ("Username", "bob", False),
        ("Password", "hunter2", True), ("TOTP", "JBSWY3DPEHPK3PXP", True),
    ]


def test_INV4_bitwarden_takes_collections_when_there_is_no_folder(tmp_path):
    _, [e] = _import(tmp_path, BITWARDEN_ORG + "\r\nShared,login,Box,,,0,,u,p,\r\n")
    assert e["category"] == "Shared"


def test_INV4_keepassxc_maps_columns(tmp_path):
    fmt, entries = _import(tmp_path, KEEPASSXC + "\r\n"
                           '"Passwords/Work/Sub","Mail","bob","pw","https://m.test","n",'
                           '"otpauth://totp/x?secret=JBSWY3DPEHPK3PXP","0","t1","t0"\r\n'
                           '"Passwords","Top","","","","","","0","",""\r\n')
    assert fmt == "keepassxc"
    mail, top = entries
    assert (mail["name"], mail["category"], mail["notes"]) == ("Mail", "Work/Sub", "n")
    assert _fields(mail) == [
        ("Username", "bob", False), ("Password", "pw", True), ("URL", "https://m.test", False),
        ("TOTP", "otpauth://totp/x?secret=JBSWY3DPEHPK3PXP", True),
    ]
    assert top["category"] == ""


def test_INV4_chromium_maps_columns(tmp_path):
    fmt, [e] = _import(tmp_path, CHROMIUM + "\r\nexample.com,https://example.com/,bob,pw,hi\r\n")
    assert fmt == "chromium"
    assert (e["name"], e.get("category", ""), e["notes"]) == ("example.com", "", "hi")
    assert _fields(e) == [("URL", "https://example.com/", False), ("Username", "bob", False),
                          ("Password", "pw", True)]


def test_INV4_firefox_maps_columns(tmp_path):
    fmt, entries = _import(tmp_path, FIREFOX + "\r\n"
                           '"https://accounts.example.org:8443/login","bob","pw","","","{g}",'
                           '"1","2","3"\r\n'
                           '"about:logins","amy","pw2","","","{h}","1","2","3"\r\n')
    assert fmt == "firefox"
    host, nohost = entries
    assert host["name"] == "accounts.example.org"
    assert _fields(host) == [("URL", "https://accounts.example.org:8443/login", False),
                             ("Username", "bob", False), ("Password", "pw", True)]
    assert nohost["name"] == "about:logins"  # no host: the URL field's value


def test_INV4_1password_maps_columns(tmp_path):
    fmt, [e] = _import(tmp_path, ONEPASSWORD + "\r\n"
                       "Bank,https://bank.test,bob,pw,otpauth://totp/b?secret=JBSWY3DPEHPK3PXP,"
                       "false,false,money,a note\r\n")
    assert fmt == "1password"
    assert (e["name"], e["notes"]) == ("Bank", "a note")
    assert _fields(e) == [
        ("URL", "https://bank.test", False), ("Username", "bob", False), ("Password", "pw", True),
        ("TOTP", "otpauth://totp/b?secret=JBSWY3DPEHPK3PXP", True), ("Tags", "money", False),
    ]


def test_INV4_an_empty_name_falls_back_to_the_url_then_untitled(tmp_path):
    _, entries = _import(tmp_path, CHROMIUM + "\r\n,https://a.test,bob,pw\r\n,,amy,pw\r\n")
    assert [e["name"] for e in entries] == ["https://a.test", "Untitled"]


def test_INV5_unknown_column_becomes_field(tmp_path):
    _, [e] = _import(tmp_path, CHROMIUM + ",Recovery email\r\nx,https://x.test,bob,pw,,r@x.test\r\n")
    assert ("Recovery email", "r@x.test", False) in _fields(e)


def test_INV6_totp_field_always_sensitive(tmp_path):
    assert not rolodex.is_sensitive_label("TOTP")
    assert rolodex.parse_totp_field("TOTP", "not-a-seed") is None
    _, [e] = _import(tmp_path, KEEPASSXC + '\r\n"Root","X","","","","","not-a-seed","0","",""\r\n')
    assert _fields(e) == [("TOTP", "not-a-seed", True)]


def test_a_field_value_is_never_stripped(tmp_path):
    _, [e] = _import(tmp_path, CHROMIUM + '\r\nx,,bob," pw "\r\n')
    assert ("Password", " pw ", True) in _fields(e)


def test_entries_from_mapping_follows_the_chosen_targets(tmp_path):
    header = ["Site", "Login", "Folder", "Secret", "Junk"]
    rows = [["Mail", "bob", "Work", "pw", "zzz"], ["", "amy", "", "", ""]]
    entries = rolodex.entries_from_mapping(header, rows,
                                           ["name", "field", "category", "field", "skip"])
    assert entries[0]["name"] == "Mail" and entries[0]["category"] == "Work"
    assert _fields(entries[0]) == [("Login", "bob", False), ("Secret", "pw", True)]
    assert entries[1]["name"] == "Untitled" and _fields(entries[1]) == [("Login", "amy", False)]


# --- INV-7 -------------------------------------------------------------------------------


def test_INV7_malformed_file_raises_value_error(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        rolodex.read_csv_file(_write(tmp_path, "a,b\r\n1,2,3\r\n"))

    monkeypatch.setattr(rolodex, "MAX_IMPORT_BYTES", 100)
    with pytest.raises(ValueError, match="too large"):
        rolodex.read_csv_file(_write(tmp_path, "a,b\r\n" + "x" * 200 + ",y\r\n", "big.csv"))
    monkeypatch.undo()

    long_cell = "n" * 200_000
    header, rows = rolodex.read_csv_file(_write(tmp_path, f'a,b\r\n"{long_cell}",y\r\n', "l.csv"))
    assert rows == [[long_cell, "y"]]


def test_read_csv_file_pads_short_rows_and_drops_empty_ones(tmp_path):
    header, rows = rolodex.read_csv_file(
        _write(tmp_path, "﻿a,b,c\r\n1\r\n,,\r\n\r\n4,5,6\r\n"))
    assert header == ["a", "b", "c"]  # the byte-order mark is gone
    assert rows == [["1", "", ""], ["4", "5", "6"]]
    assert rolodex.read_csv_file(_write(tmp_path, "", "empty.csv")) == ([], [])


# --- INV-8 -------------------------------------------------------------------------------


def test_INV8_categories_from_file():
    vault = {"version": 2, "categories": ["Games"], "entries": {}}
    rolodex.add_entry(vault, "Dup", [])
    parsed = [
        {"name": "A", "fields": [], "notes": "", "category": "Work"},
        {"name": "Dup", "fields": [], "notes": "", "category": "Old"},  # skipped duplicate
        {"name": "B", "fields": [], "notes": "", "category": "Work"},
        {"name": "C", "fields": [], "notes": "", "category": "Games"},
        {"name": "D", "fields": [], "notes": ""},
    ]
    assert rolodex.import_entries(vault, parsed, category=None) == (4, 1)
    assert vault["categories"] == ["Games", "Work"]
    by_name = {e["name"]: e.get("category", "") for e in vault["entries"].values()}
    assert by_name == {"Dup": "", "A": "Work", "B": "Work", "C": "Games", "D": ""}


def test_import_keeps_parsed_timestamps_and_stamps_the_rest():
    vault = {"version": 2, "categories": [], "entries": {}}
    rolodex.import_entries(vault, [
        {"name": "Old", "fields": [], "notes": "", "created": "2020-01-01T00:00:00",
         "modified": "2020-02-02T00:00:00"},
        {"name": "New", "fields": [], "notes": ""},
    ])
    by_name = {e["name"]: e for e in vault["entries"].values()}
    assert by_name["Old"]["created"] == "2020-01-01T00:00:00"
    assert by_name["Old"]["modified"] == "2020-02-02T00:00:00"
    assert by_name["New"]["created"].startswith("20") and by_name["New"]["created"] != "2020"


def test_a_rolodex_row_drops_an_unparseable_timestamp_and_hides_an_unreadable_flag(tmp_path):
    _, [e] = _import(tmp_path, ROLODEX + "\r\nX,,,not a date,2020-01-01T00:00:00,Label,v,maybe\r\n")
    assert "created" not in e and e["modified"] == "2020-01-01T00:00:00"
    assert _fields(e) == [("Label", "v", True)]


# --- INV-13 ------------------------------------------------------------------------------


def _vault_of(n):
    vault = {"version": 2, "categories": [], "entries": {}}
    for i in range(n):
        rolodex.add_entry(vault, f"e{i}", [])
    return vault


def test_INV13_export_warns_over_import_limits():
    assert rolodex.csv_export_exceeds_import_limits(_vault_of(rolodex.MAX_IMPORT_ENTRIES + 1))
    assert not rolodex.csv_export_exceeds_import_limits(_vault_of(rolodex.MAX_IMPORT_ENTRIES))
    one = _vault_of(1)
    next(iter(one["entries"].values()))["notes"] = "x" * (rolodex.MAX_IMPORT_BYTES + 1)
    assert rolodex.csv_export_exceeds_import_limits(one)
