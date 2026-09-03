"""GUI-layer regression tests for ROLO-0059 — secrets left behind in widget buffers.

Unlike tests/test_regressions.py, which is deliberately GTK-free, these construct real
widgets. They still need no display and no Xvfb: each dialog is built and its handlers
called directly, so nothing is ever presented and no main loop runs. Run with: pytest tests/
"""

import pytest

import rolodex

from gi.repository import Adw


PW = "correct horse battery staple"


@pytest.fixture(scope="module")
def app():
    """One Adw.Application to satisfy Gtk.Window's `application` property. It is never
    started; UnlockDialog's own `self.app` is replaced per-test with a recording stub."""
    return Adw.Application(application_id="org.rolodex.Tests")


class FakeApp:
    """Stands in for RolodexApp so a success path stops before MainWindow is built."""

    def __init__(self):
        self.opened = None

    def open_main(self, vault, salt, password, path, key):
        self.opened = (vault, salt, password, path, key)


class FakeMainWindow:
    """Stands in for MainWindow for the dialogs that write back through it."""

    def __init__(self, vault=None):
        self.vault = vault or {"version": 2, "categories": [], "entries": {}}
        self._restore_path = "/nonexistent/backup.vault"
        self.added = None
        self.edited = None
        self.restored = None

    def _finish_add(self, name, fields, notes, category):
        self.added = (name, fields, notes, category)

    def _finish_edit(self, entry_id, name, fields, notes, category):
        self.edited = (entry_id, name, fields, notes, category)

    def _finish_restore(self, vault, salt, password, key):
        self.restored = (vault, salt, password, key)


# --- UnlockDialog -------------------------------------------------------------------------


def test_ROLO0059_unlock_dialog_wipes_the_password_after_a_successful_unlock(app, tmp_path):
    """The entry buffer held the master password for as long as the dialog lived, so
    MainWindow._lock's "Wipe secrets from memory" was an overstatement -- the same plaintext
    was still reachable through the widget that took it."""
    dlg = rolodex.UnlockDialog(app, str(tmp_path / "v.vault"), is_new=False)
    dlg.app = FakeApp()
    dlg.pw_entry.set_text(PW)

    dlg._unlock_ok({"version": 2, "categories": [], "entries": {}}, b"\x00" * 16, PW, b"k")

    assert dlg.app.opened is not None, "the unlock must still have handed the vault over"
    assert dlg.pw_entry.get_text() == ""


def test_ROLO0059_unlock_dialog_wipes_both_entries_after_creating_a_vault(app, tmp_path,
                                                                         monkeypatch):
    """The create path holds the master password twice -- pw_entry and pw_confirm."""
    created = {}

    def fake_create(pw, path):
        created["pw"] = pw
        return {"version": 2, "categories": [], "entries": {}}, b"\x00" * 16, b"k"

    monkeypatch.setattr(rolodex, "create_vault_with_key", fake_create)

    dlg = rolodex.UnlockDialog(app, str(tmp_path / "new.vault"), is_new=True)
    dlg.app = FakeApp()
    dlg.pw_entry.set_text(PW)
    dlg.pw_confirm.set_text(PW)

    dlg._on_activate()

    assert created["pw"] == PW, "the password must still have reached create_vault_with_key"
    assert dlg.pw_entry.get_text() == ""
    assert dlg.pw_confirm.get_text() == ""


def test_ROLO0059_unlock_dialog_keeps_the_password_after_a_failed_unlock(app, tmp_path):
    """master-password.md INV-7: a wrong password refocuses the field. Wiping on failure
    would hand the user an empty box to "correct", so the wipe is a success-path action."""
    dlg = rolodex.UnlockDialog(app, str(tmp_path / "v.vault"), is_new=False)
    dlg.pw_entry.set_text(PW)

    dlg._unlock_fail("Wrong password.")

    assert dlg.pw_entry.get_text() == PW


# --- RestorePasswordDialog ----------------------------------------------------------------


def test_ROLO0059_restore_dialog_wipes_the_backup_password_on_success():
    win = FakeMainWindow()
    dlg = rolodex.RestorePasswordDialog(win)
    dlg.pw_entry.set_text(PW)
    # _unlock_ok bails out unless the dialog is on screen; nothing here is ever presented.
    dlg.get_presented = lambda: True

    dlg._unlock_ok({"version": 2, "categories": [], "entries": {}}, b"\x00" * 16, PW, b"k")

    assert win.restored is not None, "the restore must still have been handed over"
    assert dlg.pw_entry.get_text() == ""


# --- AddEditDialog ------------------------------------------------------------------------


def test_ROLO0059_add_edit_dialog_wipes_field_values_on_save():
    """The wipe must land after the values have been read, or it would silently drop the
    secret being saved -- entries-and-fields.md INV-5 keeps a field on a non-empty value."""
    win = FakeMainWindow()
    dlg = rolodex.AddEditDialog(win, "Add")
    dlg.name_entry.set_text("Example")
    rows = dlg._get_field_rows()
    rows[0].value_entry.set_text("alice")
    rows[1].value_entry.set_text("s3cr3t-value")

    dlg._on_save(None)

    assert win.added is not None, "the entry must still have been saved"
    saved = {f["label"]: f["value"] for f in win.added[1]}
    assert saved["Password"] == "s3cr3t-value", "the wipe must not precede the read"
    assert [r.value_entry.get_text() for r in dlg._get_field_rows()] == ["", ""]


def test_ROLO0059_add_edit_dialog_wipes_field_values_when_closed_without_saving():
    """Closing an editor over an existing entry left every secret it had loaded sitting in
    the row buffers. This is the not-dirty close; the discard-confirm path closes through
    the same wrapper."""
    entry = {
        "name": "Example",
        "category": "",
        "fields": [{"label": "Password", "value": "s3cr3t-value", "sensitive": True}],
        "notes": "",
    }
    win = FakeMainWindow({"version": 2, "categories": [], "entries": {"id1": entry}})
    dlg = rolodex.AddEditDialog(win, "Edit", entry_id="id1", entry=entry)
    assert [r.value_entry.get_text() for r in dlg._get_field_rows()] == ["s3cr3t-value"]

    dlg._on_close_attempt(None)

    assert win.added is None and win.edited is None, "an unsaved close must not commit"
    assert [r.value_entry.get_text() for r in dlg._get_field_rows()] == [""]
