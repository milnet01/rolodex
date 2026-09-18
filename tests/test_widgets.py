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

    def open_main(self, vault, salt, password, path, key, lock=None):
        self.opened = (vault, salt, password, path, key)
        self.lock = lock


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


# --- ImportPreviewDialog (ROLO-0047, ROLO-0067) -------------------------------------------


class ImportRecorder(FakeMainWindow):
    def __init__(self, vault):
        super().__init__(vault)
        self.imported = None

    def _finish_import(self, parsed, category=""):
        self.imported = (parsed, category)


def test_ROLO0047_preview_marks_in_file_duplicates_and_a_ticked_one_is_handed_over():
    vault = {"version": 2, "categories": ["Games"], "entries": {}}
    rolodex.add_entry(vault, "Bank", [])
    win = ImportRecorder(vault)
    parsed = [{"name": n, "fields": [], "notes": ""} for n in ("Mail", "bank", "mail")]
    dlg = rolodex.ImportPreviewDialog(win, parsed, "/x.txt")
    assert [c.get_active() for c, _ in dlg.checks] == [True, False, False]
    dlg.checks[1][0].set_active(True)  # the user ticks the duplicate on purpose
    dlg._on_import(None)
    assert [e["name"] for e in win.imported[0]] == ["Mail", "bank"]
    assert win.imported[1] == ""


def test_ROLO0067_preview_passes_the_chosen_category():
    vault = {"version": 2, "categories": ["Email", "Games"], "entries": {}}
    win = ImportRecorder(vault)
    dlg = rolodex.ImportPreviewDialog(win, [{"name": "Steam", "fields": [], "notes": ""}], "/x")
    dlg.cat_row.set_selected(2)
    dlg._on_import(None)
    assert win.imported[1] == "Games"


# --- UnlockDialog: vault lock and recovery routes (ROLO-0044, ROLO-0045) -------------------


def test_ROLO0044_second_unlock_screen_is_refused_while_the_vault_is_open(app, tmp_path, monkeypatch):
    path = str(tmp_path / "v.vault")
    rolodex.create_vault(PW, path)
    holder = rolodex.VaultLock(path)
    holder.acquire()
    started = []
    import threading
    monkeypatch.setattr(threading, "Thread", lambda **k: started.append(k) or pytest.fail("ran"))
    dlg = rolodex.UnlockDialog(app, path, is_new=False)
    dlg.pw_entry.set_text(PW)
    dlg._on_activate()
    assert "already open" in dlg.status.get_text()
    assert started == []
    holder.release()


def _run_try_unlock(dlg, pw, monkeypatch):
    calls = []
    monkeypatch.setattr(rolodex.GLib, "idle_add", lambda fn, *a: calls.append((fn, a)))
    dlg._try_unlock(pw)
    fn, args = calls[0]
    fn(*args)


def test_ROLO0045_an_unreadable_vault_offers_restore_and_new(app, tmp_path, monkeypatch):
    path = tmp_path / "v.vault"
    path.write_bytes(b"not a vault at all")
    dlg = rolodex.UnlockDialog(app, str(path), is_new=False)
    assert not dlg.recover_box.get_visible()
    _run_try_unlock(dlg, PW, monkeypatch)
    assert dlg.recover_box.get_visible()
    assert not dlg.lock.held


def test_ROLO0045_a_wrong_password_offers_nothing(app, tmp_path, monkeypatch):
    path = str(tmp_path / "v.vault")
    rolodex.create_vault(PW, path)
    dlg = rolodex.UnlockDialog(app, path, is_new=False)
    _run_try_unlock(dlg, "wrong password!!", monkeypatch)
    assert dlg.status.get_text() == "Wrong password."
    assert not dlg.recover_box.get_visible()


# --- MainWindow._write_vault: the change check (ROLO-0044) ---------------------------------


class _WriterWin:
    def __init__(self, path):
        self.vault_path = path
        self._disk_fingerprint = rolodex.vault_fingerprint(path)


def test_ROLO0044_write_vault_refuses_when_the_file_changed_underneath(tmp_path):
    path = str(tmp_path / "v.vault")
    vault, salt, key = rolodex.create_vault_with_key(PW, path)
    win = _WriterWin(path)
    rolodex.MainWindow._write_vault(win, vault, key, salt)  # our own write: fine
    rolodex.MainWindow._write_vault(win, vault, key, salt)  # and again
    other = rolodex.load_vault_with_key(PW, path)
    rolodex.add_entry(other[0], "Written elsewhere", [])
    rolodex.save_vault_with_key(other[0], other[2], other[1], path)
    with pytest.raises(rolodex.VaultChangedError):
        rolodex.MainWindow._write_vault(win, vault, key, salt)
    loaded, _ = rolodex.load_vault(PW, path)
    assert [e["name"] for e in loaded["entries"].values()] == ["Written elsewhere"]


def test_ROLO0044_save_asks_instead_of_overwriting(tmp_path):
    asked = []

    class Win(_WriterWin):
        vault = {"version": 2, "categories": [], "entries": {}}
        _key = b""
        salt = b""

        def _write_vault(self, *a):
            raise rolodex.VaultChangedError()

        def _confirm_overwrite_changed_vault(self):
            asked.append(True)

    assert rolodex.MainWindow._save(Win(str(tmp_path / "v"))) is False
    assert asked == [True]
