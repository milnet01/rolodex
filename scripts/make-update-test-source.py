#!/usr/bin/env python3
"""Write a TEST copy of rolodex.py for the live Windows update test (ROLO-0042 section 4.6).

    python3 scripts/make-update-test-source.py SRC OUT --key B64 --feed OWNER/REPO --version V

The copy differs from SRC in four literals only: __version__, RELEASE_PUBLIC_KEY_B64,
GITHUB_OWNER and GITHUB_REPO. A binary built from it trusts a throwaway key and fetches from a
throwaway feed, so the shipped update code can be exercised end to end without the release key.

There is deliberately no runtime override of the key or feed: anything that let an environment
variable, flag or config key change either would let whoever controls it install code. A test
build is a different binary instead, and this script refuses anything that could make it look
like a release:

  * any of the four assignments does not occur exactly once at module scope;
  * --key is the committed release key;
  * --feed is milnet01/rolodex (in any case) or not OWNER/REPO;
  * --version is not N(.N)*.

Only .github/workflows/update-test-build.yml runs this; build.yml must never name it.
"""

import argparse
import re
import sys

RELEASE_FEED = "milnet01/rolodex"
NAMES = ("__version__", "RELEASE_PUBLIC_KEY_B64", "GITHUB_OWNER", "GITHUB_REPO")


def _assignment(name: str) -> re.Pattern:
    return re.compile(rf'^{re.escape(name)} = "([^"\\]*)"$', re.M)


def make(source: str, key: str, feed: str, version: str) -> str:
    """Return the test source, or raise ValueError naming what was refused."""
    found = {}
    for name in NAMES:
        matches = _assignment(name).findall(source)
        if len(matches) != 1:
            raise ValueError(f"{name} = \"...\" occurs {len(matches)} times, not once")
        found[name] = matches[0]
    if key == found["RELEASE_PUBLIC_KEY_B64"]:
        raise ValueError("--key is the committed release key")
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", key):
        raise ValueError("--key is not base64")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", feed):
        raise ValueError("--feed must be OWNER/REPO")
    if feed.lower() == RELEASE_FEED:
        raise ValueError(f"--feed is the release feed {RELEASE_FEED}")
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)*", version):
        raise ValueError("--version must be N(.N)*")
    owner, repo = feed.split("/")
    values = {"__version__": version, "RELEASE_PUBLIC_KEY_B64": key,
              "GITHUB_OWNER": owner, "GITHUB_REPO": repo}
    for name, value in values.items():
        source = _assignment(name).sub(lambda _m, n=name, v=value: f'{n} = "{v}"', source)
    return source


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src")
    parser.add_argument("out")
    parser.add_argument("--key", required=True, help="test public key, base64 raw Ed25519")
    parser.add_argument("--feed", required=True, help="OWNER/REPO of the test release feed")
    parser.add_argument("--version", required=True, help="the test build's version, N(.N)*")
    args = parser.parse_args(argv)
    with open(args.src, encoding="utf-8") as fh:
        source = fh.read()
    try:
        made = make(source, args.key, args.feed, args.version)
    except ValueError as exc:
        print(f"make-update-test-source: refused: {exc}", file=sys.stderr)
        return 1
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write(made)
    print(f"make-update-test-source: wrote {args.out} ({args.feed}, {args.version})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
