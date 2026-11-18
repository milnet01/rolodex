"""Translation catalogs that must track the code (ROLO-0028).

scripts/update-translations.sh rebuilds po/rolodex.pot from rolodex.py, merges it into each
po/<lang>.po and compiles each into locale/<lang>/LC_MESSAGES/rolodex.mo. These tests fail when
a step was skipped: a string added to the code but not the template, a language missing a
translation, or a .po edited without recompiling the .mo the app actually loads.
"""

import ast
import gettext
import os
import re

import pytest

import rolodex

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POT = os.path.join(REPO, "po", "rolodex.pot")
LANGUAGES = ["ar", "de", "es", "fr", "he", "ja", "zh_CN"]
PLACEHOLDER = re.compile(r"\{(\w*)\}")


def _unquote(line):
    return ast.literal_eval(line)


def _parse_po(path):
    """Entries as dicts {msgid, plural, msgstr: [..], fuzzy}; the header and obsolete '#~'
    entries are left out. Only what xgettext and msgmerge write: no msgctxt."""
    entries, cur, field = [], None, None

    def flush():
        if cur is not None and cur["msgid"]:
            entries.append(cur)

    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#~"):
                continue
            if line.startswith("#,"):
                flush()
                cur, field = {"msgid": "", "plural": None, "msgstr": {}, "fuzzy": False}, None
                cur["fuzzy"] = "fuzzy" in line
                continue
            if line.startswith("#"):
                continue
            if line.startswith("msgid "):
                if cur is None or field is not None:
                    flush()
                    cur = {"msgid": "", "plural": None, "msgstr": {}, "fuzzy": False}
                cur["msgid"], field = _unquote(line[6:]), "msgid"
            elif line.startswith("msgid_plural "):
                cur["plural"], field = _unquote(line[13:]), "plural"
            elif line.startswith("msgstr["):
                idx = int(line[7:line.index("]")])
                cur["msgstr"][idx], field = _unquote(line[line.index("]") + 2:]), idx
            elif line.startswith("msgstr "):
                cur["msgstr"][0], field = _unquote(line[7:]), 0
            elif line.startswith('"'):
                if field in ("msgid", "plural"):
                    cur[field] += _unquote(line)
                else:
                    cur["msgstr"][field] += _unquote(line)
    flush()
    return entries


def _keys(entries):
    return {(e["msgid"], e["plural"]) for e in entries}


def _source_strings():
    """Every literal msgid rolodex.py passes to _() or ngettext(), as xgettext extracts them."""
    with open(os.path.join(REPO, "rolodex.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        args = [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
                for a in node.args]
        if node.func.id == "_" and len(args) == 1 and args[0]:
            found.add((args[0], None))
        elif node.func.id == "ngettext" and len(args) == 3 and args[0] and args[1]:
            found.add((args[0], args[1]))
    return found


def test_ROLO0028_template_matches_the_code():
    # A string wrapped in the code but missing from the template never reaches a translator.
    assert _keys(_parse_po(POT)) == _source_strings()


def _nplurals(path):
    """The plural-form count the catalog's header declares: 2 for German, 1 for Japanese."""
    with open(path, encoding="utf-8") as fh:
        found = re.search(r"Plural-Forms: nplurals=(\d+);", fh.read())
    assert found, f"{path} declares no Plural-Forms"
    return int(found.group(1))


@pytest.mark.parametrize("lang", LANGUAGES)
def test_ROLO0028_catalog_translates_every_template_string(lang):
    path = os.path.join(REPO, "po", f"{lang}.po")
    entries = _parse_po(path)
    assert _keys(entries) == _keys(_parse_po(POT))
    nplurals = _nplurals(path)
    for e in entries:
        assert not e["fuzzy"], e["msgid"]
        expected = nplurals if e["plural"] is not None else 1
        assert len(e["msgstr"]) == expected and all(e["msgstr"].values()), e["msgid"]
        # A placeholder the translation drops or renames raises KeyError at .format() time.
        # A one-form language must carry the plural's placeholders too.
        wanted = set(PLACEHOLDER.findall(e["msgid"] + (e["plural"] or "")))
        for text in e["msgstr"].values():
            assert set(PLACEHOLDER.findall(text)) == wanted, (e["msgid"], text)


@pytest.mark.parametrize("lang", LANGUAGES)
def test_ROLO0028_compiled_catalog_matches_its_po(lang):
    # The app loads the .mo, not the .po: an edit without a recompile never reaches a user.
    with open(os.path.join(rolodex.LOCALE_DIR, lang, "LC_MESSAGES", "rolodex.mo"), "rb") as fh:
        mo = gettext.GNUTranslations(fh)
    for e in _parse_po(os.path.join(REPO, "po", f"{lang}.po")):
        if e["plural"] is None:
            assert mo.gettext(e["msgid"]) == e["msgstr"][0], e["msgid"]
        else:
            for n in (1, 5):
                assert mo.ngettext(e["msgid"], e["plural"], n) == e["msgstr"][mo.plural(n)], \
                    (e["msgid"], n)


@pytest.mark.parametrize("lang, rtl", [("he", True), ("ar", True), ("de", False),
                                       ("ja", False)])
def test_ROLO0101_layout_mirrors_for_a_right_to_left_catalog(lang, rtl):
    with open(os.path.join(rolodex.LOCALE_DIR, lang, "LC_MESSAGES", "rolodex.mo"), "rb") as fh:
        assert rolodex.catalog_is_rtl(gettext.GNUTranslations(fh)) is rtl


def test_ROLO0101_english_fallback_stays_left_to_right():
    # A Persian desktop gets English text (no catalog ships), so the window must not mirror.
    assert rolodex.catalog_is_rtl(gettext.NullTranslations()) is False


@pytest.mark.parametrize("apple_locale, expected", [
    ("zh-Hans_CN", ["zh_CN"]),  # macOS puts the script between language and region
    ("zh-Hans", ["zh_CN"]),
    ("zh-Hant_TW", ["zh_TW"]),  # Traditional must not fall to the Simplified catalog
    ("ja_JP", ["ja_JP"]),
    ("de_DE@rg=dezzzz", ["de_DE"]),
])
def test_ROLO0100_macos_locale_with_a_script_finds_its_catalog(monkeypatch, apple_locale,
                                                               expected):
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(rolodex.sys, "platform", "darwin")
    monkeypatch.setattr(rolodex.subprocess, "run", lambda *a, **k: rolodex.subprocess.
                        CompletedProcess(a, 0, stdout=apple_locale + "\n", stderr=""))
    assert rolodex._os_ui_languages() == expected
