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
LANGUAGES = ["de", "es", "fr"]
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


@pytest.mark.parametrize("lang", LANGUAGES)
def test_ROLO0028_catalog_translates_every_template_string(lang):
    entries = _parse_po(os.path.join(REPO, "po", f"{lang}.po"))
    assert _keys(entries) == _keys(_parse_po(POT))
    for e in entries:
        assert not e["fuzzy"], e["msgid"]
        expected = 2 if e["plural"] is not None else 1
        assert len(e["msgstr"]) == expected and all(e["msgstr"].values()), e["msgid"]
        # A placeholder the translation drops or renames raises KeyError at .format() time.
        wanted = set(PLACEHOLDER.findall(e["msgid"]))
        for text in e["msgstr"].values():
            assert set(PLACEHOLDER.findall(text)) == wanted, (e["msgid"], text)


@pytest.mark.parametrize("lang", LANGUAGES)
def test_ROLO0028_compiled_catalog_matches_its_po(lang):
    # The app loads the .mo, not the .po: an edit without a recompile never reaches a user.
    with open(os.path.join(rolodex.LOCALE_DIR, lang, "LC_MESSAGES", "rolodex.mo"), "rb") as fh:
        mo = gettext.GNUTranslations(fh)
    one, many = 1, 5
    for e in _parse_po(os.path.join(REPO, "po", f"{lang}.po")):
        if e["plural"] is None:
            assert mo.gettext(e["msgid"]) == e["msgstr"][0], e["msgid"]
        else:
            assert mo.ngettext(e["msgid"], e["plural"], one) == e["msgstr"][0], e["msgid"]
            assert mo.ngettext(e["msgid"], e["plural"], many) == e["msgstr"][1], e["msgid"]
