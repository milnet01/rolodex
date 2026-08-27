"""Conformance tests for the opt-in signed auto-update (ROLO-0037).

Contract: docs/specs/ROLO-0037-auto-update.md. Each test names the invariant it pins.

Nothing here touches the network: every test injects a fake fetcher. Nothing uses the real
signing key: a throwaway Ed25519 key is monkeypatched in where a valid signature is needed.

A pytest process is never frozen, so every test that expects an offer must FAKE one --
`frozen` below does that. Without it INV-1, INV-4, INV-5 and INV-6 would all assert an offer
that INV-2's gate correctly refuses, and the cheapest way to make them pass is weakening that
gate, which is the one change this spec must not invite (spec section 7).
"""

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import rolodex


# --- helpers ------------------------------------------------------------------------------


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Simulate a frozen Linux build, so is_update_supported() is True."""
    binary = tmp_path / "rolodex-linux-x86_64"
    binary.write_bytes(b"old binary")
    monkeypatch.setattr(rolodex.sys, "frozen", True, raising=False)
    monkeypatch.setattr(rolodex.sys, "executable", str(binary))
    monkeypatch.setattr(rolodex.sys, "platform", "linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    return binary


def release(tag="v9.9.9", asset="rolodex-linux-x86_64", with_sig=True, extra=None, body="notes"):
    assets = [{"name": asset, "browser_download_url": f"https://example.invalid/{asset}"}]
    if with_sig:
        assets.append(
            {"name": asset + ".sig", "browser_download_url": f"https://example.invalid/{asset}.sig"}
        )
    assets.extend(extra or [])
    return {"tag_name": tag, "assets": assets, "body": body}


class CountingFetcher:
    """Records how many times it was called — the only way to prove INV-1's 'no network call'."""

    def __init__(self, payload=None, raises=None):
        self.calls = 0
        self._payload = payload if payload is not None else release()
        self._raises = raises

    def __call__(self, *a, **kw):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._payload


# --- INV-1: opt-in, and it must not even call the fetcher ---------------------------------


@pytest.mark.parametrize("stored", [None, False, "true", "yes", 1, 0, ""])
def test_INV1_disabled_makes_no_network_call(frozen, tmp_path, stored):
    """Absent, false, or ANY malformed value reads as off — and the fetcher is never called."""
    conf = tmp_path / ".conf"
    if stored is not None:
        conf.write_text(json.dumps({"check_for_updates": stored}))
    fetcher = CountingFetcher()

    assert rolodex.check_for_update(fetcher=fetcher, config_path=str(conf)) is None
    assert fetcher.calls == 0, "a disabled check must not reach the network at all"


def test_INV1_enabled_does_call_the_fetcher(frozen, tmp_path):
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))
    fetcher = CountingFetcher()

    info = rolodex.check_for_update(fetcher=fetcher, config_path=str(conf), current_version="1.0.0")

    assert fetcher.calls == 1
    assert info is not None and info.version == "9.9.9"


def test_INV1_preference_round_trips(tmp_path):
    """The setter is what the in-app toggle calls, and it must produce a value the getter
    reads as on. Nothing tested this until the toggle existed, and the setter had no caller
    at all -- so the preference gated a path no user could reach."""
    conf = tmp_path / ".conf"
    assert rolodex.update_check_enabled(str(conf)) is False  # absent reads as off

    rolodex.set_update_check_enabled(True, str(conf))
    assert rolodex.update_check_enabled(str(conf)) is True
    assert json.loads(conf.read_text())["check_for_updates"] is True

    rolodex.set_update_check_enabled(False, str(conf))
    assert rolodex.update_check_enabled(str(conf)) is False


def test_INV1_both_check_paths_are_wired(tmp_path):
    """Source scan: the silent (unforced) path and the manual (forced) path must BOTH have a
    call site. With only the forced one wired the `check_for_updates` preference is inert --
    it gates a branch nothing reaches, which is exactly what shipped before this test.
    """
    source = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rolodex.py")
    with open(source, "r") as fh:
        src = fh.read()
    assert "check_for_update(force=True)" in src, "the manual check must be wired"
    assert "info = check_for_update()" in src, "the silent startup check must be wired"
    assert "set_update_check_enabled(" in src.replace("def set_update_check_enabled(", ""), (
        "the preference must have an in-app writer, or it can only be set by editing JSON"
    )


# --- INV-2: inert off a frozen build, and on Windows -------------------------------------


def test_INV2_unfrozen_build_is_inert(monkeypatch, tmp_path):
    monkeypatch.setattr(rolodex.sys, "frozen", False, raising=False)
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))
    fetcher = CountingFetcher()

    assert rolodex.detect_installer() is None
    assert rolodex.is_update_supported() is False
    assert rolodex.check_for_update(fetcher=fetcher, config_path=str(conf)) is None
    assert fetcher.calls == 0


def test_INV2_windows_is_refused_even_when_frozen(monkeypatch, tmp_path):
    """Windows is deferred (S4) and this mechanism does not work there, so it is refused
    BEFORE any offer rather than after a download."""
    monkeypatch.setattr(rolodex.sys, "frozen", True, raising=False)
    monkeypatch.setattr(rolodex.sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))
    fetcher = CountingFetcher()

    assert rolodex.platform_asset_name() is None
    assert rolodex.is_update_supported() is False
    assert rolodex.check_for_update(fetcher=fetcher, config_path=str(conf)) is None
    assert fetcher.calls == 0


def test_INV2_intel_mac_gets_no_asset(monkeypatch):
    """An arm64-only build must not be offered to an x86_64 Mac — matching on sys.platform
    alone would do exactly that, and the install would replace a working binary."""
    monkeypatch.setattr(rolodex.sys, "platform", "darwin")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    assert rolodex.platform_asset_name() is None

    monkeypatch.setattr("platform.machine", lambda: "arm64")
    assert rolodex.platform_asset_name() == "rolodex-macos-arm64"


# --- INV-4: the check needs no vault -------------------------------------------------------


def test_INV4_check_runs_with_no_vault_present(frozen, tmp_path):
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))
    assert not (tmp_path / "contacts.vault").exists()

    info = rolodex.check_for_update(
        fetcher=CountingFetcher(), config_path=str(conf), current_version="1.0.0"
    )
    assert info is not None


# --- INV-5 / D10: what is offered ----------------------------------------------------------


@pytest.mark.parametrize(
    "tag,current,expected",
    [
        ("v9.9.9", "1.0.0", True),   # newer
        ("v1.0.0", "1.0.0", False),  # equal
        ("v0.9.0", "1.0.0", False),  # older
        ("v1.2-rc1", "1.0.0", False),  # malformed
        ("latest", "1.0.0", False),    # not a version at all
        ("1_0.0", "1.0.0", False),     # int() would accept "1_0"; isdigit() must not
        ("v0.1", "0.1.0", False),      # zero-padded equal, not newer
        ("v0.1.1", "0.1", True),       # zero-padded strictly greater
    ],
)
def test_INV5_offer_table(frozen, tmp_path, tag, current, expected):
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))
    info = rolodex.check_for_update(
        fetcher=CountingFetcher(release(tag=tag)),
        config_path=str(conf),
        current_version=current,
    )
    assert (info is not None) is expected


def test_INV5_missing_signature_asset_is_never_offered(frozen, tmp_path):
    """No .sig means no offer at all — never an unsigned install."""
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))
    info = rolodex.check_for_update(
        fetcher=CountingFetcher(release(with_sig=False)),
        config_path=str(conf),
        current_version="1.0.0",
    )
    assert info is None


def test_INV5_duplicate_asset_fails_safe(frozen, tmp_path):
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))
    dupe = [{"name": "rolodex-linux-x86_64", "browser_download_url": "https://example.invalid/x"}]
    info = rolodex.check_for_update(
        fetcher=CountingFetcher(release(extra=dupe)),
        config_path=str(conf),
        current_version="1.0.0",
    )
    assert info is None


def test_INV5_matching_is_equality_not_prefix():
    """The release's own required .sig is a PREFIX match of the asset name. If the predicate
    used startswith, the ambiguity guard would fire on every well-formed release and no update
    would ever be offered — while a synthetic duplicate-asset test still passed."""
    assets = release()["assets"]
    assert rolodex.select_update_assets(assets, "rolodex-linux-x86_64") is not None
    prefix_matches = [a for a in assets if a["name"].startswith("rolodex-linux-x86_64")]
    assert len(prefix_matches) == 2, "the .sig is a prefix match; equality is what saves us"


# --- INV-6: force bypasses the opt-in gate and nothing else -------------------------------


def test_INV6_force_bypasses_only_the_optin_gate(frozen, tmp_path):
    conf = tmp_path / ".conf"  # not written: the feature is OFF
    fetcher = CountingFetcher()

    assert rolodex.check_for_update(fetcher=fetcher, config_path=str(conf)) is None
    assert fetcher.calls == 0

    info = rolodex.check_for_update(
        force=True, fetcher=fetcher, config_path=str(conf), current_version="1.0.0"
    )
    assert fetcher.calls == 1 and info is not None


def test_INV6_force_still_honours_skip(frozen, tmp_path):
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"skipped_update_version": "9.9.9"}))
    info = rolodex.check_for_update(
        force=True, fetcher=CountingFetcher(), config_path=str(conf), current_version="1.0.0"
    )
    assert info is None


# --- INV-7: skip persists, Later does not --------------------------------------------------


def test_INV7_skip_persists_and_a_later_version_is_still_offered(frozen, tmp_path):
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))

    rolodex.skip_update_version("9.9.9", str(conf))
    assert rolodex.update_skipped_version(str(conf)) == "9.9.9"

    same = rolodex.check_for_update(
        fetcher=CountingFetcher(release(tag="v9.9.9")),
        config_path=str(conf),
        current_version="1.0.0",
    )
    assert same is None

    higher = rolodex.check_for_update(
        fetcher=CountingFetcher(release(tag="v9.9.10")),
        config_path=str(conf),
        current_version="1.0.0",
    )
    assert higher is not None

    # The skip lands in the plaintext config and nowhere near the vault.
    assert json.loads(conf.read_text())["skipped_update_version"] == "9.9.9"


# --- INV-8 / INV-11: signature verification -----------------------------------------------


@pytest.fixture
def signing_key(monkeypatch):
    """A throwaway key monkeypatched in as the release key — the real one is never used."""
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(rolodex, "release_public_key", key.public_key)
    return key


def _info(tmp_path):
    return rolodex.UpdateInfo("9.9.9", "https://e.invalid/a", "https://e.invalid/a.sig", "")


def _downloader(payload, signature):
    def fetch(url, dest, max_bytes):
        with open(dest, "wb") as fh:
            fh.write(signature if url.endswith(".sig") else payload)

    return fetch


def test_INV8_good_signature_returns_the_verified_bytes(frozen, tmp_path, signing_key):
    payload = b"the new binary"
    result = rolodex.download_and_verify(
        _info(tmp_path),
        downloader=_downloader(payload, signing_key.sign(payload)),
        target=str(frozen),
    )
    with open(result, "rb") as fh:
        assert fh.read() == payload


@pytest.mark.parametrize("tamper", ["payload", "signature"])
def test_INV8_a_one_byte_tamper_raises_and_leaves_no_temp(frozen, tmp_path, signing_key, tamper):
    payload = b"the new binary"
    signature = signing_key.sign(payload)
    if tamper == "payload":
        payload = b"the new binari"
    else:
        signature = bytes([signature[0] ^ 1]) + signature[1:]

    with pytest.raises(rolodex.UpdateVerificationError):
        rolodex.download_and_verify(
            _info(tmp_path), downloader=_downloader(payload, signature), target=str(frozen)
        )

    # INV-8: no staged temp is left beside the target. NOT "the directory is empty" —
    # INV-10 stages beside the target binary, so the directory always contains that.
    leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".rolodex-update-")]
    assert leftovers == []


def test_INV11_shipped_key_is_the_all_zero_placeholder(frozen, tmp_path):
    """This is the half that works. A throwaway key's signature fails against ANY other key,
    so asserting only 'it raises' stays green whether the placeholder or a real production key
    is shipped — it would never detect that the key had, or had not, been replaced.

    This test is MEANT to fail the day a real signing key lands, so INV-11 is retired in the
    same commit that makes the feature functional.
    """
    assert base64.b64decode(rolodex.RELEASE_PUBLIC_KEY_B64) == bytes(32)

    key = Ed25519PrivateKey.generate()
    payload = b"anything"
    with pytest.raises(rolodex.UpdateVerificationError):
        rolodex.download_and_verify(
            _info(tmp_path),
            downloader=_downloader(payload, key.sign(payload)),
            target=str(frozen),
        )


# --- INV-9: bounds and scheme --------------------------------------------------------------


def test_INV9_non_https_urls_are_refused():
    for url in ("http://example.invalid/x", "file:///etc/passwd", "ftp://x/y", ""):
        with pytest.raises(rolodex.UpdateError):
            rolodex._require_https(url)
    rolodex._require_https("https://example.invalid/x")  # does not raise


def test_INV9_caps_have_headroom_over_real_assets():
    """A cap under the real artifact aborts every genuine update while a synthetic over-cap
    test still passes, so the floor is what matters here."""
    assert rolodex.MAX_UPDATE_BYTES >= 100 * 1024 * 1024
    assert rolodex.MAX_SIG_BYTES >= 64  # a raw Ed25519 signature
    assert rolodex.UPDATE_TIMEOUT_S > 0


# --- INV-12: network confinement -----------------------------------------------------------


def test_INV12_no_module_scope_network_import():
    """Scan for these names and NO wider one. rolodex.py imports `urllib.parse` at module
    scope for TOTP otpauth:// parsing — a scan for bare `urllib` fails on correct pre-existing
    code, and the cheapest way to make it pass is deleting that import, which breaks TOTP.
    """
    source = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rolodex.py")
    with open(source, "r") as fh:
        lines = fh.read().splitlines()

    banned = ("import urllib.request", "import urllib.error", "import socket", "import http")
    offenders = [
        (n, line)
        for n, line in enumerate(lines, 1)
        if not line[:1].isspace() and any(line.startswith(b) for b in banned)
    ]
    assert offenders == [], f"module-scope network import: {offenders}"

    # The expected one is still there and must stay.
    assert any(line.startswith("import urllib.parse") for line in lines)


def test_INV12_importing_rolodex_does_not_load_urllib_request():
    """Assert that name and no wider one: urllib, socket and ssl are ALL present after import,
    via urllib.parse and via GTK and cryptography."""
    import sys as _sys

    assert "rolodex" in _sys.modules  # imported at the top of this file
    assert "urllib.request" not in _sys.modules


# --- INV-13: failures are silent unforced, distinguishable when forced --------------------


@pytest.mark.parametrize(
    "error", [OSError("dns"), ValueError("malformed json"), KeyError("nope"), TimeoutError()]
)
def test_INV13_check_failures_are_silent_on_the_startup_path(frozen, tmp_path, error):
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))
    assert (
        rolodex.check_for_update(
            fetcher=CountingFetcher(raises=error), config_path=str(conf), current_version="1.0.0"
        )
        is None
    )


def test_INV13_forced_failure_is_distinguishable_from_up_to_date(frozen, tmp_path):
    """None alone conflates 'up to date' with 'could not check'. A button the user pressed on
    purpose must not answer a DNS failure with 'You're up to date'."""
    conf = tmp_path / ".conf"
    conf.write_text(json.dumps({"check_for_updates": True}))

    with pytest.raises(rolodex.UpdateError):
        rolodex.check_for_update(
            force=True,
            fetcher=CountingFetcher(raises=OSError("dns")),
            config_path=str(conf),
            current_version="1.0.0",
        )

    # ...while a genuine "up to date" still returns None under force.
    assert (
        rolodex.check_for_update(
            force=True,
            fetcher=CountingFetcher(release(tag="v1.0.0")),
            config_path=str(conf),
            current_version="1.0.0",
        )
        is None
    )


# --- INV-10 / INV-14: install and relaunch -------------------------------------------------


def test_INV10_temp_is_staged_beside_the_target(frozen, tmp_path, signing_key):
    payload = b"new"
    result = rolodex.download_and_verify(
        _info(tmp_path), downloader=_downloader(payload, signing_key.sign(payload)), target=str(frozen)
    )
    assert os.path.dirname(result) == os.path.dirname(str(frozen)), (
        "the temp must share a filesystem with the target, or os.replace cannot be atomic"
    )


def test_INV10_failure_before_replace_leaves_the_binary_intact(frozen, tmp_path, monkeypatch):
    original = frozen.read_bytes()
    new_file = tmp_path / "staged"
    new_file.write_bytes(b"replacement")

    def boom(*a, **kw):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(rolodex.os, "replace", boom)
    with pytest.raises(rolodex.UpdateError):
        rolodex.apply_update(str(new_file), target=str(frozen))

    assert frozen.read_bytes() == original
    assert not new_file.exists(), "a failed install must not orphan its temp"


def test_INV14_swap_then_relaunch_then_exit(frozen, tmp_path, monkeypatch):
    order = []
    new_file = tmp_path / "staged"
    new_file.write_bytes(b"replacement")

    monkeypatch.setattr(rolodex.subprocess, "Popen", lambda *a, **kw: order.append("relaunch"))
    monkeypatch.setattr(rolodex.os, "_exit", lambda code: order.append(f"exit{code}"))

    rolodex.apply_update(str(new_file), target=str(frozen), on_before_exec=lambda: order.append("wipe"))

    assert frozen.read_bytes() == b"replacement", "the swap must have committed"
    assert order == ["wipe", "relaunch", "exit0"]


def test_INV14_a_failed_relaunch_still_exits(frozen, tmp_path, monkeypatch):
    """The swap already committed, so we must never return into a live window whose binary
    changed underneath it — a manual restart gets the new version."""
    order = []
    new_file = tmp_path / "staged"
    new_file.write_bytes(b"replacement")

    def boom(*a, **kw):
        raise OSError("no fork")

    monkeypatch.setattr(rolodex.subprocess, "Popen", boom)
    monkeypatch.setattr(rolodex.os, "_exit", lambda code: order.append(f"exit{code}"))

    rolodex.apply_update(str(new_file), target=str(frozen))
    assert order == ["exit0"]


def test_D9_relaunch_env_resets_the_bootloader_and_restores_loader_paths(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI12345")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    env = rolodex._relaunch_env()
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["LD_LIBRARY_PATH"] == "/usr/lib", "must restore the SYSTEM path, not the _MEI one"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_D9_relaunch_env_drops_loader_path_when_there_was_none(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI12345")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    assert "LD_LIBRARY_PATH" not in rolodex._relaunch_env()


def test_D9_relaunch_command_waits_for_the_old_pid(frozen):
    cmd = rolodex._relaunch_command(str(frozen), 4242)
    assert cmd[0] == "/bin/sh"
    script = cmd[2]
    assert "kill -0 4242" in script, "must wait for the OLD process to tear down first"
    assert "exec" in script
    assert "-ge 600" in script, "the wait must be hard-capped so a wedged process cannot hang it"
