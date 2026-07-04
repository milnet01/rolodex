# Dependency Management Standards

## Policy: latest by default

**All dependencies are kept on the latest stable version** — this applies to new features and
to security updates equally. Do not wait for something to break before upgrading; staleness is
itself a risk.

This covers everything Rolodex depends on:

- **Python runtime** — target the current stable CPython (3.10+ minimum for the idioms used).
- **`cryptography`** — the one security-critical PyPI dependency. Track its releases and skim
  its changelog when bumping (Fernet / PBKDF2 behaviour especially).
- **GTK 4 / libadwaita** — system-provided; keep current via the distro. Note the minimum
  versions the code relies on (GTK 4, libadwaita 1).
- **Dev/CI tooling** — linters, test runners, GitHub Actions, and runner images.

### Sweep posture

Check, don't wait. When doing any dependency-adjacent work (touching `requirements.txt`, CI,
or adding a dep), run the freshness check on the way past:

```bash
python3 -m pip list --outdated          # PyPI deps
python3 -c "import cryptography; print(cryptography.__version__)"   # current crypto
# newest cryptography on PyPI:
python3 -m pip index versions cryptography | head -1
```

When bumping a dependency, **update the calling code to the current idioms in the same change**
(see `docs/coding-standards.md`), and record the bump in `CHANGELOG.md`.

## The one allowed exception: a forced-older pin

A dependency may be held at an older version **only if** a newer version explicitly breaks a
Rolodex feature **and** there is no other way to keep the feature working.

When that happens, all of the following are mandatory:

1. **Pin with an inline reason** in `requirements.txt` (or the relevant manifest) — a one-line
   comment stating what breaks and pointing at the ledger entry.
2. **Add a ledger entry** in the table below.
3. **Never silently pin.** An undocumented `==` pin is a standards violation.

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

Because the app has no automated tests yet (roadmap ROLO-0001), a dependency bump is verified
by manually exercising the affected flow. For a `cryptography` bump specifically, at minimum:

1. Create a fresh vault, add an entry, quit.
2. Re-launch and unlock — confirms KDF + Fernet round-trip across the new version.
3. Back up and restore — confirms the file-format path.

Once ROLO-0001 lands, these become automated and run in CI on every dependency bump.
