#!/usr/bin/env python3
"""Check every release signature against the public key built into rolodex.py.

    python3 scripts/verify-release-sigs.py <rolodex.py> <artifact dir>

The release job runs this after signing and before publishing. A signing secret that is not
the private half of RELEASE_PUBLIC_KEY_B64 (a bad paste, a regenerated key) still signs
without error, and every shipped install then refuses the update. This makes that a failed
release instead. Needs only `cryptography`: rolodex.py is read as text, never imported,
because importing it needs GTK.

Exit 0 when every artifact has a .sig that verifies, or when none has one (signing was
skipped because the secret is unset; the job has already warned). Exit 1 otherwise.
"""

import ast
import base64
import glob
import os
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def shipped_public_key(source_path: str) -> Ed25519PublicKey:
    with open(source_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "RELEASE_PUBLIC_KEY_B64"):
            return Ed25519PublicKey.from_public_bytes(base64.b64decode(node.value.value))
    raise SystemExit(f"no RELEASE_PUBLIC_KEY_B64 assignment in {source_path}")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    key = shipped_public_key(argv[1])
    artifacts = sorted(p for p in glob.glob(os.path.join(argv[2], "*")) if not p.endswith(".sig"))
    if not artifacts:
        print(f"no artifacts under {argv[2]}", file=sys.stderr)
        return 1
    if not any(os.path.exists(p + ".sig") for p in artifacts):
        print("no signatures present: signing was skipped, nothing to verify")
        return 0
    failed = 0
    for path in artifacts:
        try:
            with open(path + ".sig", "rb") as fh:
                signature = fh.read()
            with open(path, "rb") as fh:
                key.verify(signature, fh.read())
            print(f"verified {path}")
        except FileNotFoundError:
            print(f"MISSING signature for {path}", file=sys.stderr)
            failed += 1
        except InvalidSignature:
            print(f"BAD signature for {path}: the signing secret does not match "
                  "RELEASE_PUBLIC_KEY_B64 in rolodex.py", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
