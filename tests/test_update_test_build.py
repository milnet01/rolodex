"""ROLO-0042 INV-11: test builds stay apart from releases.

scripts/make-update-test-source.py writes a copy of rolodex.py with a throwaway key, feed and
version, for the live Windows update test. These pin what keeps that from reaching a release:
the generator's refusals, and the shape of the one workflow allowed to use it.
"""

import base64
import os
import re
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import rolodex

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATOR = os.path.join(ROOT, "scripts", "make-update-test-source.py")
SOURCE = os.path.join(ROOT, "rolodex.py")
TEST_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "update-test-build.yml")
BUILD_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "build.yml")


def _key():
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _run(src, out, key=None, feed="someone/rolodex-update-test", version="0.0.2"):
    return subprocess.run(
        [sys.executable, GENERATOR, str(src), str(out), "--key", key or _key(),
         "--feed", feed, "--version", version],
        check=False, capture_output=True, text=True)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_generator_replaces_exactly_the_four_literals(tmp_path):
    key = _key()
    out = tmp_path / "rolodex.py"
    result = _run(SOURCE, out, key=key)
    assert result.returncode == 0, result.stderr
    original, made = _read(SOURCE).splitlines(), _read(out).splitlines()
    assert len(original) == len(made)
    changed = {a: b for a, b in zip(original, made) if a != b}
    assert sorted(changed.values()) == sorted([
        '__version__ = "0.0.2"',
        f'RELEASE_PUBLIC_KEY_B64 = "{key}"',
        'GITHUB_OWNER = "someone"',
        'GITHUB_REPO = "rolodex-update-test"',
    ])


def test_generator_refuses_the_committed_key(tmp_path):
    result = _run(SOURCE, tmp_path / "o.py", key=rolodex.RELEASE_PUBLIC_KEY_B64)
    assert result.returncode != 0
    assert not (tmp_path / "o.py").exists()


@pytest.mark.parametrize("feed", ["milnet01/rolodex", "MILNET01/Rolodex", "noslash", "a/b/c"])
def test_generator_refuses_the_release_feed_and_malformed_feeds(tmp_path, feed):
    result = _run(SOURCE, tmp_path / "o.py", feed=feed)
    assert result.returncode != 0
    assert not (tmp_path / "o.py").exists()


@pytest.mark.parametrize("version", ["", "v", "1.x", "0.0.2-rc1", "1_0"])
def test_generator_refuses_an_unparseable_version(tmp_path, version):
    result = _run(SOURCE, tmp_path / "o.py", version=version)
    assert result.returncode != 0
    assert not (tmp_path / "o.py").exists()


@pytest.mark.parametrize("name", ["__version__", "RELEASE_PUBLIC_KEY_B64", "GITHUB_OWNER",
                                  "GITHUB_REPO"])
@pytest.mark.parametrize("count", [0, 2])
def test_generator_refuses_unless_each_literal_occurs_once(tmp_path, name, count):
    lines = _read(SOURCE).splitlines(keepends=True)
    pattern = re.compile(rf'^{name} = "[^"]*"\n$')
    index = next(i for i, line in enumerate(lines) if pattern.match(line))
    if count == 0:
        del lines[index]
    else:
        lines.insert(index, lines[index])
    src = tmp_path / "src.py"
    src.write_text("".join(lines), encoding="utf-8")
    result = _run(src, tmp_path / "o.py")
    assert result.returncode != 0
    assert not (tmp_path / "o.py").exists()


# --- the workflows -------------------------------------------------------------------------


def _block(text, start):
    """The lines from the one containing *start* up to the next step or comment line, at
    their own indentation stripped -- one step's body."""
    lines = text.splitlines()
    begin = next(i for i, line in enumerate(lines) if start in line)
    out = [lines[begin].strip()]
    for line in lines[begin + 1:]:
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("#") or not stripped:
            break
        out.append(stripped)
    return out


def test_test_workflow_cannot_publish_or_sign():
    text = _read(TEST_WORKFLOW)
    trigger = text.split("\npermissions:")[0]
    assert "workflow_dispatch:" in trigger
    assert "push:" not in trigger and "tags:" not in trigger and "pull_request" not in trigger
    assert re.search(r"^permissions:\n  contents: read\n", text, re.M)
    assert not re.search(r":\s*write\b", text), "no write permission of any kind"
    assert "environment:" not in text
    assert "ROLODEX_SIGNING_KEY" not in text
    assert "secrets." not in text
    assert "gh release" not in text


def test_test_workflow_builds_like_the_release_windows_job():
    test, build = _read(TEST_WORKFLOW), _read(BUILD_WORKFLOW)
    assert _block(test, "uses: msys2/setup-msys2@") == _block(build, "uses: msys2/setup-msys2@")
    pip = "python -m pip install --upgrade pyinstaller certifi"
    assert pip in build and pip in test
    assert 'bash packaging/windows-build.sh "$ASSET"' in test
    assert "make-update-test-source.py" in test


def test_release_workflow_never_names_the_generator():
    assert "make-update-test-source" not in _read(BUILD_WORKFLOW)
