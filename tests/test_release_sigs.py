"""scripts/verify-release-sigs.py: the release job's last check before publishing.

It must fail a release signed by any key other than the one rolodex.py ships, because shipped
installs refuse every update such a release offers.
"""

import base64
import os
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "verify-release-sigs.py")


def _run(source, out_dir):
    return subprocess.run([sys.executable, SCRIPT, str(source), str(out_dir)],
                          capture_output=True, text=True, check=False)


def _release(tmp_path, signer, shipped):
    """A fake rolodex.py shipping `shipped`'s public key, and two artifacts signed by `signer`."""
    pub = shipped.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    source = tmp_path / "rolodex.py"
    source.write_text(f'RELEASE_PUBLIC_KEY_B64 = "{base64.b64encode(pub).decode()}"\n')
    out = tmp_path / "out"
    out.mkdir()
    for name in ("rolodex-linux-x86_64", "rolodex.flatpak"):
        data = name.encode() * 100
        (out / name).write_bytes(data)
        (out / (name + ".sig")).write_bytes(signer.sign(data))
    return source, out


def test_signatures_from_the_shipped_key_pass(tmp_path):
    key = Ed25519PrivateKey.generate()
    result = _run(*_release(tmp_path, key, key))
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("verified") == 2


def test_signatures_from_another_key_fail_the_release(tmp_path):
    result = _run(*_release(tmp_path, Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()))
    assert result.returncode == 1
    assert "BAD signature" in result.stderr


def test_one_missing_signature_fails_the_release(tmp_path):
    key = Ed25519PrivateKey.generate()
    source, out = _release(tmp_path, key, key)
    (out / "rolodex.flatpak.sig").unlink()
    result = _run(source, out)
    assert result.returncode == 1
    assert "MISSING signature" in result.stderr


def test_unsigned_release_is_left_to_the_signing_step_warning(tmp_path):
    key = Ed25519PrivateKey.generate()
    source, out = _release(tmp_path, key, key)
    for sig in out.glob("*.sig"):
        sig.unlink()
    assert _run(source, out).returncode == 0


def test_reads_the_real_shipped_key(tmp_path):
    # A rename or reformat of the constant in rolodex.py must break this, not the release job.
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "a").write_bytes(b"x")
    result = _run(os.path.join(REPO, "rolodex.py"), tmp_path / "out")
    assert result.returncode == 0, result.stderr
