# PyInstaller build recipe for a self-contained, single-file Rolodex binary.
#
# Bundles the Python runtime, the `cryptography` dependency, and the GTK 4 / libadwaita stack
# (libraries + GObject-introspection typelibs, collected by the bundled `gi` hooks) into one
# executable. The same spec is used on Linux, Windows, and macOS via the CI workflow — the only
# per-OS difference is how the GTK runtime gets installed before this runs (see
# .github/workflows/build.yml).
#
# Build locally:  pyinstaller packaging/rolodex.spec --noconfirm

from PyInstaller.utils.hooks import collect_all

# collect_all('gi') pulls the GObject-introspection namespace: shared libs, typelibs, and the
# hidden gi.repository.* submodules. The gi runtime hook then points GI_TYPELIB_PATH at them.
datas, binaries, hiddenimports = collect_all("gi")

a = Analysis(
    ["../rolodex.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports
    + [
        "gi.repository.Gtk",
        "gi.repository.Adw",
        "gi.repository.Gdk",
        "gi.repository.Gio",
        "gi.repository.GLib",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="rolodex",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no terminal window on Windows
    # A broken bundle should exit non-zero cleanly (so the CI --selftest gate catches it) rather
    # than hang on a traceback dialog in a headless runner.
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
