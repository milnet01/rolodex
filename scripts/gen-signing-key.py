#!/usr/bin/env python3
"""Generate the Rolodex release-signing keypair (ROLO-0037, D4).

Run this ONCE. It writes the private key to a file you must keep off this repository, and
prints the public half to paste into rolodex.py's RELEASE_PUBLIC_KEY_B64.

    python3 scripts/gen-signing-key.py

Why the halves go to different places:

  * The PUBLIC half is committed into rolodex.py. It is public by definition, and baking it
    in at build time instead would mean each binary trusts whatever key the workflow happened
    to hold — which is the property signing exists to remove.

  * The PRIVATE half never enters the repository. Add it as the repository secret
    ROLODEX_SIGNING_KEY (Settings -> Secrets and variables -> Actions), and keep your local
    copy somewhere backed up. Losing it means future releases cannot be signed by the key
    already shipped in users' binaries, and there is no recovery: those binaries will refuse
    every update that follows. Treat it like the master password.

Until the real key is in place rolodex.py ships an all-zero placeholder, which loads cleanly
and verifies nothing — so the updater FAILS CLOSED rather than open. tests/test_update.py
asserts that placeholder, and is meant to fail the day you replace it, so the invariant that
records the interim state is retired in the same commit that ends it.
"""

import base64
import os
import stat
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

OUT = "rolodex-signing.key"


def main() -> int:
    if os.path.exists(OUT):
        print(f"refusing to overwrite {OUT} — move it aside first", file=sys.stderr)
        return 1

    private = Ed25519PrivateKey.generate()

    # 0600 from creation, never a chmod afterwards: a private key must not exist, even for an
    # instant, at a mode another user could read. Same rule the vault's own writes follow.
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(
            private.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    public_b64 = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode()

    mode = stat.S_IMODE(os.stat(OUT).st_mode)
    print(f"private key written to ./{OUT} (mode {mode:o})")
    print()
    print("1. Paste this into rolodex.py, replacing RELEASE_PUBLIC_KEY_B64's placeholder:")
    print()
    print(f'   RELEASE_PUBLIC_KEY_B64 = "{public_b64}"')
    print()
    print(f"2. Add the contents of ./{OUT} as the repository secret ROLODEX_SIGNING_KEY.")
    print(f"3. Move ./{OUT} somewhere backed up and OUT of this repository.")
    print()
    print("   tests/test_update.py::test_INV11_shipped_key_is_the_all_zero_placeholder will")
    print("   now fail on purpose. Retire INV-11 in the same commit that pastes the key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
