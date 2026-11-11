"""Conformance tests for the Windows in-app update (ROLO-0042).

Contract: docs/specs/ROLO-0042-windows-auto-update.md. Each test names the invariant it pins.

These run on Linux. They fake a frozen Windows build by monkeypatching sys.frozen,
sys.executable, sys.platform and platform.machine, as tests/test_update.py does for Linux.
What the helper does on a real Windows PC is checked by scripts/wintest-helper-probe.sh and
scripts/wintest-update-e2e.sh, which no gate runs (spec section 7).
"""

import ast
import hashlib
import json
import os
import re

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import rolodex

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "rolodex.py")
WIN_ASSET = "rolodex-windows-x86_64.exe"
# Windows-form paths: _windows_helper_command joins with ntpath, so these come back with "\".
WIN_DIR = r"C:\Users\someone\Rolodex app"
WIN_EXE = WIN_DIR + "\\" + WIN_ASSET
WIN_NEW = WIN_DIR + r"\.rolodex-update-abc123"
DIGEST = "ab" * 32


@pytest.fixture
def frozen_windows(monkeypatch, tmp_path):
    binary = tmp_path / WIN_ASSET
    binary.write_bytes(b"old binary")
    monkeypatch.setattr(rolodex.sys, "frozen", True, raising=False)
    monkeypatch.setattr(rolodex.sys, "executable", str(binary))
    monkeypatch.setattr(rolodex.sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    return binary


def _release(asset=WIN_ASSET, extra=()):
    names = [asset, asset + ".sig", *extra]
    return {
        "tag_name": "v9.9.9",
        "body": "",
        "assets": [{"name": n, "browser_download_url": f"https://example.invalid/{n}"}
                   for n in names],
    }


# --- INV-1: the (win32, AMD64) row, and nothing wider -------------------------------------


def test_win32_asset_row(frozen_windows, tmp_path, monkeypatch):
    assert rolodex.platform_asset_name() == WIN_ASSET
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))
    # The .sig and a .zip of the same name are prefix matches; equality must ignore both.
    info = rolodex.check_for_update(
        fetcher=lambda: _release(extra=[WIN_ASSET + ".zip"]), current_version="1.0",
        config_path=str(conf))
    assert info is not None
    assert info.asset_url.endswith("/" + WIN_ASSET)
    assert info.sig_url.endswith("/" + WIN_ASSET + ".sig")

    calls = []
    for machine in ("ARM64", "x86", "arm64", "amd64"):
        monkeypatch.setattr("platform.machine", lambda m=machine: m)
        assert rolodex.platform_asset_name() is None, machine
        assert rolodex.check_for_update(fetcher=lambda: calls.append(1),
                                        config_path=str(conf)) is None
    assert calls == [], "an unlisted Windows machine must make no request"


# --- INV-2: a frozen self-test fails on a machine with no asset ----------------------------


def _selftest_exit():
    try:
        rolodex.main()
    except SystemExit as exc:
        return exc.code or 0
    return 0


def test_selftest_fails_without_asset(frozen_windows, tmp_path, monkeypatch):
    bundle = tmp_path / "_MEI"
    (bundle / "gi_typelibs").mkdir(parents=True)
    for name in rolodex.BUNDLED_TYPELIBS:
        (bundle / "gi_typelibs" / name).write_bytes(b"")
    monkeypatch.setattr(rolodex.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(rolodex.sys, "argv", ["rolodex", "--selftest"])
    assert _selftest_exit() == 0

    monkeypatch.setattr("platform.machine", lambda: "ARM64")
    assert _selftest_exit() != 0


def test_selftest_build_yml_gates_on_the_windows_exit_code():
    """INV-2's CI half: build.yml's Windows job runs windows-build.sh, which runs the
    built .exe with --selftest under `set -e`, so a non-zero exit fails the build."""
    with open(os.path.join(ROOT, ".github", "workflows", "build.yml")) as fh:
        assert 'bash packaging/windows-build.sh "$ASSET"' in fh.read()
    with open(os.path.join(ROOT, "packaging", "windows-build.sh")) as fh:
        script = fh.read()
    assert "set -euo pipefail" in script
    assert re.search(r'^timeout \d+ "out/\$ASSET" --selftest$', script, re.M)


# --- INV-3: Windows gets only the verified bytes, with their digest ------------------------


@pytest.fixture
def signing_key(monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(rolodex, "release_public_key", key.public_key)
    return key


def _download(payload, signature):
    def fetch(url, dest, max_bytes):
        with open(dest, "wb") as fh:
            fh.write(signature if url.endswith(".sig") else payload)
    return fetch


INFO = rolodex.UpdateInfo("9.9.9", "https://e.invalid/a", "https://e.invalid/a.sig", "")


def test_staged_digest_and_tamper(frozen_windows, tmp_path, signing_key):
    payload = b"the new windows binary"
    good_sig = signing_key.sign(payload)
    staged = rolodex.download_and_verify(
        INFO, downloader=_download(payload, good_sig), target=str(frozen_windows))
    assert isinstance(staged, rolodex.StagedUpdate)
    assert staged.sha256 == hashlib.sha256(payload).hexdigest()
    with open(staged.path, "rb") as fh:
        assert fh.read() == payload
    os.unlink(staged.path)

    for bad_payload, bad_sig in ((payload[:-1] + b"X", good_sig),
                                 (payload, bytes([good_sig[0] ^ 1]) + good_sig[1:])):
        with pytest.raises(rolodex.UpdateVerificationError):
            rolodex.download_and_verify(INFO, downloader=_download(bad_payload, bad_sig),
                                        target=str(frozen_windows))
        assert [p for p in os.listdir(tmp_path) if p.startswith(".rolodex-update-")] == []


# --- INV-4: spawn, then wipe, then exit; no in-process swap --------------------------------


def test_apply_windows_order_and_flags(frozen_windows, tmp_path, monkeypatch):
    staged_file = tmp_path / ".rolodex-update-x"
    staged_file.write_bytes(b"new")
    staged = rolodex.StagedUpdate(str(staged_file), DIGEST)
    monkeypatch.setattr(rolodex.subprocess, "CREATE_NO_WINDOW", 0x1, raising=False)
    monkeypatch.setattr(rolodex.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x2, raising=False)
    monkeypatch.setattr(rolodex.subprocess, "DETACHED_PROCESS", 0x4, raising=False)
    monkeypatch.setattr(rolodex.os, "replace", lambda *a: pytest.fail("os.replace ran"))
    monkeypatch.setattr(rolodex.os, "chmod", lambda *a: pytest.fail("os.chmod ran"))
    # shutil.which consults Windows-only APIs once sys.platform says win32; not under test here.
    monkeypatch.setattr(rolodex, "_powershell_path", lambda: "powershell.exe")
    order, spawned = [], {}

    def popen(cmd, **kw):
        order.append("spawn")
        spawned.update(kw, cmd=cmd)

    monkeypatch.setattr(rolodex.subprocess, "Popen", popen)
    monkeypatch.setattr(rolodex.os, "_exit", lambda code: order.append(f"exit{code}"))

    rolodex.apply_update(staged, target=str(frozen_windows),
                         on_before_exec=lambda: order.append("wipe"))

    assert order == ["spawn", "wipe", "exit0"]
    assert spawned["creationflags"] == 0x1 | 0x2
    assert not spawned["creationflags"] & 0x4, "DETACHED_PROCESS kills PowerShell (FIBR-0444)"
    assert spawned["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert spawned["stdin"] is rolodex.subprocess.DEVNULL
    assert spawned["stderr"] is rolodex.subprocess.STDOUT
    assert spawned["close_fds"] is True
    assert DIGEST in spawned["cmd"][-1]
    assert frozen_windows.read_bytes() == b"old binary", "nothing swapped in process"
    assert (tmp_path / ".rolodex-update-helper.log").exists(), "the log sits beside the .exe"

    # A failed spawn: staged file gone, UpdateError, no wipe, no exit.
    order.clear()

    def boom(*a, **kw):
        raise OSError("blocked by policy")

    monkeypatch.setattr(rolodex.subprocess, "Popen", boom)
    with pytest.raises(rolodex.UpdateError):
        rolodex.apply_update(staged, target=str(frozen_windows),
                             on_before_exec=lambda: order.append("wipe"))
    assert order == []
    assert not staged_file.exists()


# --- INV-5 / INV-6 / INV-7 / INV-9: the helper script's shape ------------------------------


def _script(exe=WIN_EXE, new=WIN_NEW, digest=DIGEST):
    cmd = rolodex._windows_helper_command(exe, new, digest)
    assert cmd[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    assert cmd[0].lower().rstrip(".exe").endswith("powershell")
    return cmd[4]


def test_helper_script_shape():
    script = _script()
    # INV-9: the three path literals, all in the target's folder, and no other "\" literal.
    old = WIN_DIR + r"\.rolodex-update-old"
    assert f"$exe = '{WIN_EXE}'" in script
    assert f"$new = '{WIN_NEW}'" in script
    assert f"$old = '{old}'" in script
    literals = re.findall(r"'((?:[^']|'')*)'", script)
    assert sorted(lit for lit in literals if "\\" in lit) == sorted([WIN_EXE, WIN_NEW, old])

    # INV-5: the wait filters on the image path, never a PID; the timeout branch relaunches
    # nothing.
    assert "$_.Path -eq $exe" in script
    assert not re.search(r"\$PID|-Id\b|ProcessId|Wait-Process", script)
    abort = re.search(r"if \(Busy\) \{([^}]*)\}", script)
    assert abort, "no timeout branch"
    assert "Drop" in abort.group(1) and "Start-Process" not in abort.group(1)

    # INV-6: no -Force on any Move-Item; the renames run old-aside, new-in, restore.
    moves = re.findall(r"Move-Item[^;}]*", script)
    assert moves
    for move in moves:
        assert "-Force" not in move, move
    assert re.findall(r"Try5 (\$\w+) (\$\w+)", script) == [
        ("$exe", "$old"), ("$new", "$exe"), ("$old", "$exe")]

    # INV-7: the re-hash comes before the first rename, against the digest passed in.
    assert f"$want = '{DIGEST}'" in script
    assert script.index("Get-FileHash") < script.index("Try5 $exe $old")

    # INV-8: the relaunch is Start-Process; no /bin/sh anywhere.
    assert "Start-Process -FilePath $exe" in script
    assert "/bin/sh" not in script


def test_helper_quotes_apostrophes():
    folder = r"C:\Users\o'brien\Rolodex"
    script = _script(exe=folder + "\\" + WIN_ASSET, new=folder + r"\.rolodex-update-z")
    assert "$exe = 'C:\\Users\\o''brien\\Rolodex\\" + WIN_ASSET + "'" in script
    assert r"$old = 'C:\Users\o''brien\Rolodex\.rolodex-update-old'" in script


def test_helper_rejects_a_digest_that_is_not_hex():
    with pytest.raises(ValueError):
        rolodex._windows_helper_command(WIN_EXE, WIN_NEW, "x'; Remove-Item C:\\ #")


# --- INV-8: the helper environment --------------------------------------------------------


def test_helper_env(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI1")
    monkeypatch.setenv("SOMETHING", "kept")
    expected = dict(os.environ)
    expected["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    assert rolodex._windows_helper_env() == expected, "os.environ plus the flag, nothing else"


# --- INV-10: no runtime override of the key or the feed ------------------------------------

# SHA-256 of the committed RELEASE_PUBLIC_KEY_B64 literal. Changing the key is not routine
# (CLAUDE.md), so a change here must be deliberate.
PINNED_TRUST_ANCHOR_SHA256 = "0069190e619066eac94f107c715d5270cf0511d15331c3660d58167894588883"
GUARDED = ("RELEASE_PUBLIC_KEY_B64", "GITHUB_OWNER", "GITHUB_REPO")


def test_no_key_or_feed_override():
    with open(SOURCE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    assigned = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            assert not set(GUARDED) & set(node.names), "a global statement rebinds the key"
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name) and name.id in GUARDED:
                    assigned.setdefault(name.id, []).append(node)
    values = {}
    for name in GUARDED:
        nodes = assigned.get(name, [])
        assert len(nodes) == 1, f"{name} is assigned {len(nodes)} times"
        node = nodes[0]
        assert node in tree.body, f"{name} must be assigned at module scope"
        assert isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        assert isinstance(node.value.value, str), f"{name} must be a string literal"
        values[name] = node.value.value
    key_digest = hashlib.sha256(values["RELEASE_PUBLIC_KEY_B64"].encode()).hexdigest()
    assert key_digest == PINNED_TRUST_ANCHOR_SHA256
    assert f"{values['GITHUB_OWNER']}/{values['GITHUB_REPO']}" == "milnet01/rolodex"

    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for fname in ("release_public_key", "fetch_latest_release", "_require_update_url",
                  "download_and_verify"):
        body = ast.unparse(funcs[fname])
        for banned in ("environ", "getenv", "sys.argv", "load_config"):
            assert banned not in body, f"{fname} reads {banned}"

    feed_refs = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = ast.unparse(node.func)
            if callee.endswith("fetch_latest_release"):
                assert not node.args and not node.keywords, "fetch_latest_release given a feed"
            if callee.endswith("check_for_update"):
                assert "fetcher" not in {k.arg for k in node.keywords}, "a fetcher outside tests"
        if isinstance(node, ast.Name) and node.id == "fetch_latest_release":
            feed_refs += 1
    # The one bare reference is check_for_update's default `fetcher or fetch_latest_release`;
    # a second would be a new route to the feed that this test has not read.
    assert feed_refs == 1
