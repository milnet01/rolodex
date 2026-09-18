"""GUI-layer regression tests for ROLO-0059 — secrets left behind in widget buffers.

Unlike tests/test_regressions.py, which is deliberately GTK-free, these construct real
widgets. Each dialog is built and its handlers called directly, so nothing is presented and no
main loop runs -- but GTK still needs a display to build them on. A desktop session always has
one (GTK falls back to the session's Wayland socket even with DISPLAY unset); CI has none and
runs this suite under xvfb-run. Run with: pytest tests/
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
    # Creation runs on a worker thread since ROLO-0070; run it and its idle callback inline.
    import threading

    class InlineThread:
        def __init__(self, target, args=(), **_):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(threading, "Thread", InlineThread)
    monkeypatch.setattr(rolodex.GLib, "idle_add", lambda fn, *a: fn(*a))

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
        _key = b""
        salt = b""

        def __init__(self, path):
            super().__init__(path)
            self.vault = {"version": 2, "categories": [], "entries": {}}

        def _write_vault(self, *a):
            raise rolodex.VaultChangedError()

        def _confirm_overwrite_changed_vault(self):
            asked.append(True)

    assert rolodex.MainWindow._save(Win(str(tmp_path / "v"))) is False
    assert asked == [True]


# --- ROLO-0070: no silent no-ops -------------------------------------------------------------


def test_ROLO0070_saving_a_nameless_entry_highlights_the_name():
    win = FakeMainWindow()
    dlg = rolodex.AddEditDialog(win, "Add Entry")
    dlg._on_save(None)
    assert dlg.name_entry.has_css_class("error")
    assert win.added is None
    dlg.name_entry.set_text("x")
    assert not dlg.name_entry.has_css_class("error")


def test_ROLO0070_import_with_nothing_ticked_says_so():
    win = FakeMainWindow()
    win._finish_import = lambda *a: pytest.fail("imported")
    dlg = rolodex.ImportPreviewDialog(win, [{"name": "A", "fields": [], "notes": ""}], "/x")
    dlg._set_all(False)
    dlg._on_import(None)
    assert dlg.status.get_visible() and "at least one" in dlg.status.get_text()


class CategoryWin(FakeMainWindow):
    def __init__(self, vault=None):
        super().__init__(vault)
        self._collapsed_categories = set()

    def _save(self):
        return True

    def _refresh_list(self):
        pass


def test_ROLO0070_adding_a_duplicate_category_says_so():
    win = CategoryWin({"version": 2, "categories": ["Games"], "entries": {}})
    dlg = rolodex.ManageCategoriesDialog(win)
    dlg.new_cat_entry.set_text("Games")
    dlg._add_category()
    assert "already exists" in dlg.status.get_text()
    dlg.new_cat_entry.set_text("Email")
    dlg._add_category()
    assert not dlg.status.get_visible()
    assert win.vault["categories"] == ["Games", "Email"]


def test_ROLO0070_enter_submits_the_change_password_dialog():
    win = FakeMainWindow()
    win.password = PW
    dlg = rolodex.ChangePasswordDialog(win)
    dlg.current_pw.set_text("wrong")
    dlg.new_pw.emit("entry-activated")
    assert dlg.status.get_text() == "Incorrect current password."


# --- ROLO-0072: no file path in the restore error --------------------------------------------


def test_ROLO0072_restore_read_error_shows_no_path(monkeypatch):
    win = FakeMainWindow()
    win._restore_path = "/home/someone/secret-place/backup.vault"
    dlg = rolodex.RestorePasswordDialog(win)
    monkeypatch.setattr(rolodex.GLib, "idle_add", lambda fn, *a: fn(*a))
    dlg._try_unlock(PW)
    assert dlg.status.get_text() == "Could not read that backup file."
    assert "secret-place" not in dlg.status.get_text()


# --- ROLO-0053: keyboard reordering and spoken names -----------------------------------------


def _key_controller(row):
    from gi.repository import Gtk

    return [c for c in row.observe_controllers() if isinstance(c, Gtk.EventControllerKey)][0]


def test_ROLO0053_ctrl_down_moves_a_field_one_place(monkeypatch):
    from gi.repository import Gdk

    monkeypatch.setattr(rolodex.GLib, "idle_add", lambda fn, *a: None)
    dlg = rolodex.AddEditDialog(FakeMainWindow(), "Add Entry")
    for lbl in ("one", "two", "three"):
        dlg.fields_listbox.append(rolodex.FieldRow(dlg, lbl, "v"))
    rows = dlg._get_field_rows()
    first = [r for r in rows if r.label_entry.get_text() == "one"][0]
    _key_controller(first).emit("key-pressed", Gdk.KEY_Down, 0, Gdk.ModifierType.CONTROL_MASK)
    ours = ("one", "two", "three")  # a new entry also starts with default fields
    labels = [r.label_entry.get_text() for r in dlg._get_field_rows()
              if r.label_entry.get_text() in ours]
    assert labels == ["two", "one", "three"]


def test_ROLO0053_ctrl_up_moves_a_category_and_saves(monkeypatch):
    from gi.repository import Gdk

    monkeypatch.setattr(rolodex.GLib, "idle_add", lambda fn, *a: None)
    win = CategoryWin({"version": 2, "categories": ["A", "B", "C"], "entries": {}})
    dlg = rolodex.ManageCategoriesDialog(win)
    row_c = [r for r in dlg.cat_listbox if r.cat_name == "C"][0]
    _key_controller(row_c).emit("key-pressed", Gdk.KEY_Up, 0, Gdk.ModifierType.CONTROL_MASK)
    assert win.vault["categories"] == ["A", "C", "B"]


def test_ROLO0053_icon_buttons_carry_an_accessible_label():
    from gi.repository import Gtk

    win = CategoryWin({"version": 2, "categories": ["Games"], "entries": {}})
    dlg = rolodex.ManageCategoriesDialog(win)
    row = [r for r in dlg.cat_listbox if isinstance(r, rolodex.CategoryRow)][0]
    labelled = []

    def walk(w):
        if isinstance(w, Gtk.Button) and w.get_icon_name():
            labelled.append(w)
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(row)
    assert len(labelled) == 2
    # Gtk exposes no getter for an accessible property, so assert through the helper's call.
    seen = []
    orig = rolodex.a11y_label
    rolodex.a11y_label = lambda w, t: seen.append(t) or orig(w, t)
    try:
        rolodex.CategoryRow(dlg, "Games", 3)
    finally:
        rolodex.a11y_label = orig
    assert seen == ["3 entries", "Rename category Games", "Delete category Games"]


# --- ROLO-0026: reopen on the entry that was open last ------------------------------------


def test_ROLO0026_main_window_reopens_the_remembered_entry(app, tmp_path, monkeypatch):
    conf = tmp_path / "conf"
    monkeypatch.setattr(rolodex, "CONFIG_FILE", str(conf))
    path = str(tmp_path / "v.vault")
    vault, salt, key = rolodex.create_vault_with_key(PW, path)
    rolodex.add_entry(vault, "Alpha", [])
    beta = rolodex.add_entry(vault, "Beta", [])
    rolodex.save_config({rolodex.LAST_ENTRY_KEY: beta})
    win = rolodex.MainWindow(app, vault, salt, PW, path, key)
    assert win._current_entry_id == beta
    # An id the vault no longer holds is ignored rather than raising.
    rolodex.save_config({rolodex.LAST_ENTRY_KEY: "gone"})
    win2 = rolodex.MainWindow(app, vault, salt, PW, path, key)
    assert win2._current_entry_id is None


# --- ROLO-0009: the sidebar category filter -----------------------------------------------


def _window_with(app, tmp_path, monkeypatch, categories):
    monkeypatch.setattr(rolodex, "CONFIG_FILE", str(tmp_path / "conf"))
    path = str(tmp_path / "v.vault")
    vault, salt, key = rolodex.create_vault_with_key(PW, path)
    vault["categories"] = list(categories)
    rolodex.add_entry(vault, "Steam", [], category="Games" if categories else "")
    rolodex.add_entry(vault, "Wi-Fi", [])
    return rolodex.MainWindow(app, vault, salt, PW, path, key)


def _listed(win):
    return [r.entry_id for r in win.listbox if isinstance(r, rolodex.EntryRow)]


def test_ROLO0009_filter_shows_one_category_and_hides_without_categories(app, tmp_path,
                                                                          monkeypatch):
    win = _window_with(app, tmp_path, monkeypatch, ["Games"])
    assert win.category_filter.get_visible()
    win.category_filter.set_selected(1)  # "Games"
    names = [win.vault["entries"][e]["name"] for e in _listed(win)]
    assert names == ["Steam"]
    assert win.count_label.get_text() == "1 of 2 entries"
    win.category_filter.set_selected(2)  # "Uncategorised"
    assert [win.vault["entries"][e]["name"] for e in _listed(win)] == ["Wi-Fi"]
    # Deleting the filtered category falls back to all entries.
    win.category_filter.set_selected(1)
    rolodex.delete_category(win.vault, "Games")
    win._refresh_list()
    assert not win.category_filter.get_visible()
    assert len(_listed(win)) == 2

    other = tmp_path / "plain"
    other.mkdir()
    plain = _window_with(app, other, monkeypatch, [])
    assert not plain.category_filter.get_visible()
