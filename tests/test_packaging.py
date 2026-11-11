"""Packaging files that must track the code (ROLO-0010, ROLO-0027)."""

import os
import re
import xml.etree.ElementTree as ET

import rolodex

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METAINFO = os.path.join(REPO, "packaging", "com.rolodex.Contacts.metainfo.xml")


def _changelog_releases():
    with open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8") as fh:
        return re.findall(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", fh.read(), re.M)


def test_ROLO0027_metainfo_lists_every_changelog_release_with_its_date():
    # A software centre shows these as the release history; a version the CHANGELOG has and
    # the metainfo lacks is a release that silently never appears there.
    releases = ET.parse(METAINFO).getroot().find("releases")
    listed = [(r.get("version"), r.get("date")) for r in releases.findall("release")]
    assert listed == _changelog_releases()


def test_ROLO0027_metainfo_newest_release_is_the_running_version():
    newest = ET.parse(METAINFO).getroot().find("releases").find("release").get("version")
    assert newest == rolodex.__version__


def test_ROLO0010_metainfo_id_matches_the_app_id():
    # The Flatpak app id, the desktop file name and GApplication's id must all be one string,
    # or the software centre and the desktop treat them as different apps.
    root = ET.parse(METAINFO).getroot()
    assert root.findtext("id") == rolodex.APP_ID
    assert root.findtext("launchable") == f"{rolodex.APP_ID}.desktop"
    assert os.path.isfile(os.path.join(REPO, "packaging", "flatpak", f"{rolodex.APP_ID}.desktop"))
