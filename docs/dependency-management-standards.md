# Dependency Management Standards

## Policy: latest by default

**All dependencies are kept on the latest stable version** — this applies to new features and
to security updates equally. Do not wait for something to break before upgrading; staleness is
itself a risk.

This covers everything Rolodex depends on:

- **Python runtime** — target the current stable CPython (3.10+ minimum for the idioms used).
- **`cryptography`** — the one security-critical dependency in `requirements.txt`. Track its
  releases and skim its changelog when bumping (Fernet / PBKDF2 behaviour especially).
- **Bundled build-time deps** — `certifi` and PyInstaller, installed by
  `.github/workflows/build.yml` and named as prerequisites in `packaging/*-build.sh`, rather
  than coming from `requirements.txt`. `certifi` is security-critical too: it is the CA bundle
  the frozen binaries' TLS trust depends on, so a stale one ships *inside* the released binary.
  The freshness check below covers it.
- **GTK 4 / libadwaita** — system-provided; keep current via the distro. Note the minimum
  versions the code relies on (GTK 4, libadwaita 1).
- **Dev/CI tooling** — linters, test runners, GitHub Actions, and runner images. GitHub Actions
  are pinned to a commit SHA carrying a trailing `# vX.Y.Z` comment, deliberately, for
  supply-chain integrity. That is **not** a forced-older pin and owes no ledger row: the
  obligation on it is to move the SHA to each new release under the latest-by-default rule and
  keep the comment accurate. Repointing an action at a floating tag to "comply" is the breach,
  not the fix.

### Sweep posture

Check, don't wait. When doing any dependency-adjacent work (touching `requirements.txt`, CI,
or adding a dep), run the freshness check on the way past:

```bash
python3 -m pip list --outdated          # PyPI deps
python3 -c "import cryptography; print(cryptography.__version__)"   # current crypto
# newest cryptography on PyPI (pip index is an experimental subcommand and prints a
# warning; if it changes, `pip install cryptography==` also lists available versions):
python3 -m pip index versions cryptography | head -1
python3 -c "import certifi; print(certifi.__version__)"   # bundled CA bundle, build-time only
```

When bumping a dependency, **update the calling code to the current idioms in the same change**
(see `docs/coding-standards.md`), and record the bump in `CHANGELOG.md`. That `CHANGELOG`
obligation fires on a change to a tracked file — a raised floor, a new or lifted pin, a new
dependency, or a moved action SHA. Upgrading only your own environment changes no shipped
artefact, and is recorded only if it changes shipped behaviour.

## The one allowed exception: a forced-older pin

A dependency may be held at an older version **only if** a newer version explicitly breaks a
Rolodex feature **and** there is no other way to keep the feature working.

When that happens, all of the following are mandatory:

1. **Pin with an inline reason** in `requirements.txt` (or the relevant manifest) — a one-line
   comment stating what breaks and pointing at the ledger entry.
2. **Apply the pin everywhere that dependency is installed**, and name each place in the ledger
   row. `requirements.txt` alone is not enough: `.github/workflows/ci.yml` and
   `.github/workflows/build.yml` install their dependencies directly with `pip install
   --upgrade`, so a pin they do not carry is silently ignored and CI stays green against a
   version nobody is shipping.
3. **Add a ledger entry** in the table below.
4. **Never silently pin.** An undocumented `==` pin is a standards violation.

**This exception cannot take a dependency below a security floor.** `requirements.txt` sets
`cryptography >= 44.0.0` because older releases carry known CVEs, and `security-standards.md`
§ Dependencies & supply chain makes that floor override this exception — no pin below it is
permitted, ledger entry or not. A break fixable only by going below such a floor is a release blocker, not a
forced-older pin.

## Known-incompatible versions ledger

This ledger is how we recover from forced pins. Each entry records the version that broke a
feature and the last-known-good version we held at. **When a version *newer* than the "broken
at" version is released, re-test the feature** — if it's fixed, lift the pin, bump to latest,
and move the row to "Resolved".

### Active pins

| Dependency | Broken at (version) | Feature it breaks | Held at | Re-test when > | Noted (date) |
|------------|--------------------|--------------------|---------|----------------|--------------|
| _(none)_ | — | — | — | — | — |

### Resolved (pin lifted)

| Dependency | Was broken at | Feature | Fixed in | Resolved (date) |
|------------|---------------|---------|----------|-----------------|
| _(none)_ | — | — | — | — |

> There are currently **no forced-older pins** — every dependency is on latest. This table is a
> template kept ready so that the first time we hit an incompatibility, the process is obvious.

## Verifying an upgrade

The pure-logic test suite (`pytest tests/`) runs in CI on every push to `main` and every pull
request targeting `main` (ROLO-0001 / ROLO-0020), so a dependency bump's KDF + Fernet round-trip
and vault migration are checked automatically once it reaches one of those. A bump pushed to a
topic branch triggers no CI at all — run `./CI-local.sh` there.
For a `cryptography` bump specifically, also manually exercise the affected flow, at minimum:

1. Create a fresh vault, add an entry, quit.
2. Re-launch and unlock — confirms KDF + Fernet round-trip across the new version.
3. Back up and restore — confirms the file-format path.

The automated suite catches a broken round-trip on `main` and on pull requests; the manual steps
above cover the file-format and GUI-adjacent paths a unit test doesn't reach.
