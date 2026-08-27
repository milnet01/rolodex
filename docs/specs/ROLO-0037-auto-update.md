# Spec: Opt-in Signed Auto-Update (ROLO-0037)

**Status:** draft (2026-08-27)

**Roadmap:** ROLO-0037

**Prior art:** finbreak `docs/specs/FIBR-0054.md` (the updater) and `FIBR-0131.md` (the Windows
plug), at `/mnt/Games/Scripts/Linux/finbreak`. This spec is a port, not a copy — the differences
are called out in § 4 and are mostly consequences of Rolodex shipping a PyInstaller one-file
binary where finbreak ships an AppImage.

## 1. Goal

Let a Rolodex user learn that a newer release exists and install it from inside the app, without
the update path ever becoming a way to get unsigned code onto the machine that holds their
credentials.

## 2. Problem

Rolodex publishes self-contained binaries for Linux, Windows and macOS on every `v*` tag
(`.github/workflows/build.yml`). Nothing tells a user a new one exists. They find out by
visiting GitHub, or they never find out — so a security fix like 1.3.1's atomic vault write
reaches only the users who happen to look.

Two things stand in the way, and both are prerequisites rather than details:

- **The running app does not know its own version.** `.claude/bump.json` states this outright:
  *"Rolodex carries its version in CHANGELOG.md + the git tag only (no `__version__` in the
  single-file app)."* An updater cannot compare against a version the process cannot read.
- **Releases are not signed.** `build.yml` attaches binaries with no signature. An updater that
  installs an unverified download is a remote-code-execution path into a password manager — the
  worst possible place to have one. Signing is therefore in scope here, not a follow-up.

### 2.1 This contradicts a stated goal, deliberately

`DESIGN.md`'s first **Goal** — not its non-goals, which say nothing about the network — is
*"A local, offline, encrypted store for credentials."*, elaborated as *"One master password, one
file, on your machine. No account, no cloud, no network access of any kind."*

An update check is network access. The conflict is real, and is resolved by narrowing the goal
rather than by pretending it does not apply: the feature is off by default (INV-1), it
is the app's only egress and never transmits vault-derived data (INV-3), and `DESIGN.md` is
amended in the same change to state the carve-out. Shipping code that contradicts the design
document leaves two sources of truth, and the one users read is the one nobody checked.

**That amendment changes direction for work still to come, so it takes `CLAUDE.md` rule 14's
gate on its own account.** This spec's gate does not cover it.

## 3. Scope decisions (agreed with the user)

The user asked for "an auto-update feature like what has been implemented with the finbreak
app". These are the decisions taken in reading that across to Rolodex; the ones marked
**assumed** were not put to the user and are flagged again in § 9 and § 10.

| # | Decision | Basis |
|---|----------|-------|
| S1 | Port finbreak's shape rather than invent one | Explicit user request naming finbreak |
| S2 | Opt-in, off by default | **Assumed.** finbreak's own shape, and the only reading that survives § 2.1's conflict with `DESIGN.md` |
| S3 | Ed25519 signature verification is mandatory, not a later hardening step | **Assumed.** An unsigned updater in a password manager is a remote-code-execution path; see § 8 |
| S4 | Linux and macOS now; Windows deferred to its own item | Windows needs an out-of-process swap and cannot be tested from this machine at all |
| S5 | The user generates and holds the signing key; the repo never contains it | Nothing else is safe, and it is not the session's key to make |

## 4. Design

**D1 — The install seam is the frozen binary, detected via `sys.frozen`.** finbreak keys its
Linux path off `$APPIMAGE`; Rolodex has no AppImage. A PyInstaller one-file build sets
`sys.frozen = True` and puts the running binary at `sys.executable` on every platform. Absent
`sys.frozen` there is no installer and the feature is inert.

**D2 — Asset names come from the existing build matrix and are not invented here.**
`build.yml` already fixes them: `rolodex-linux-x86_64`, `rolodex-macos-arm64`,
`rolodex-windows-x86_64.exe`. The signature asset is that name + `.sig`. The Linux and macOS
assets carry no file extension, so the staged temp has none either — harmless, because the file
is `chmod +x`'d and `os.replace`d rather than opened by extension.

**D3 — Ed25519 detached signatures, verified against a key baked into the binary.**
`cryptography` is already Rolodex's one dependency and provides Ed25519, so this adds none. The
signature is the raw 64 bytes over the exact asset bytes, published as `<asset>.sig`.

**D4 — The private key never enters the repository.** `scripts/gen-signing-key.py` generates the
pair; the private half becomes a GitHub Actions secret and lives nowhere else. Until that key
exists the public constant holds base64 of 32 zero bytes: the module imports cleanly, key
loading succeeds, and no real signature can verify — so the feature **fails closed** in the
interim rather than failing open (INV-11).

**D5 — Preferences live in `.rolodex.conf`, not the vault.** That file is already plaintext JSON
holding non-secret preferences (`idle_lock_seconds`, `clipboard_clear_seconds`). Two keys join
it: `check_for_updates` (bool, default `false`) and `skipped_update_version` (string). They must
be readable without the vault, because the check runs at startup while the app is locked
(INV-4); putting them in the vault would make the update check depend on the user having
unlocked, which is the wrong coupling.

**D6 — Network code is confined and lazily imported.** finbreak confines network access to one
module and greps for `import urllib` everywhere else. Rolodex is a single file, so that shape is
not available. Instead `urllib` is imported *inside* the fetch functions and never at module
scope, so `import rolodex` pulls in no network stack and a test can assert exactly that (INV-12).
**This is the weaker of the two mechanisms**: it proves the import graph, not that some future
function fails to add its own egress.

**D7 — CA trust: `certifi` when present, the system store otherwise.** finbreak hit a real bug
here — a binary built on one distro, run on another whose CA bundle sits elsewhere, found no CAs
and the check failed silently. Rolodex has the same exposure, because its binaries are built on
`ubuntu-latest` and run anywhere. But a second hard dependency fights the one-dependency goal,
so `certifi` is a **build-time** dependency, bundled into the frozen binaries where the problem
exists, and imported defensively — a source checkout uses the system store, which is correct
there. `requirements.txt` is unchanged.

**D8 — `__version__` is introduced in `rolodex.py` and becomes a version-bearing file.**
`.claude/bump.json` gains it, so `post_check` enforces lockstep between it, the CHANGELOG
heading and the tag. This is the prerequisite from § 2 and the one change that touches the
release recipe.

**D9 — The relaunch waits for the old process to exit.** A PyInstaller one-file binary unpacks
itself into a private `_MEI` directory and cleans it up on exit. Spawning the replacement before
the old process has torn down makes the new bootloader collide with the old extraction
directory — finbreak shipped this bug twice. The relaunch is therefore a detached `/bin/sh` that
polls until the old PID is gone (hard-capped, so a wedged process cannot hang it forever), then
`exec`s the swapped binary with `PYINSTALLER_RESET_ENVIRONMENT=1` and the loader variables
restored from PyInstaller's own `*_ORIG` values.

**D10 — Version grammar is `N(.N)*` with an explicit digit guard.** A leading `v`/`V` is
stripped; every remaining segment must satisfy `segment.isascii() and segment.isdigit()`.
`int()` is **not** the guard — it silently accepts `"1_0"`, `" 1"`, `"+1"` and Unicode digits.
Comparison zero-pads the shorter tuple, so `0.1` and `0.1.0` compare equal.

## 5. Invariants

- **INV-1** `check_for_update()` returns `None` and makes **no network call** unless
  `check_for_updates` in `.rolodex.conf` is exactly boolean `true`. Absent (a fresh install),
  `false`, or any malformed value all read as off.
  *Test:* `tests/test_update.py` — an injected fetcher records its call count; asserted zero for
  absent / `false` / `"yes"` / `1`, and non-zero once explicitly enabled.
- **INV-2** Off a frozen build (`sys.frozen` unset), `detect_installer()` returns `None`,
  `is_update_supported()` is `False`, and `check_for_update()` returns `None` before any network
  call.
  *Test:* `tests/test_update.py` — with `sys.frozen` unset, assert all three and a zero fetcher
  call count.
- **INV-3** The check transmits nothing derived from the vault: the request carries a fixed
  `User-Agent`, no query parameters, no cookies and no identifier.
  *Test:* `tests/test_update.py` — the injected fetcher captures the URL and headers; assert the
  header set exactly and that the URL is the bare `/releases/latest` endpoint.
- **INV-4** The check requires no vault: it reads `.rolodex.conf`, `__version__` and the network
  alone, and runs correctly while the app is locked.
  *Test:* `tests/test_update.py` — run the check with a config path whose sibling vault file does
  not exist; assert an `UpdateInfo` is still produced.
- **INV-5** An update is offered only for a release that is **all** of: a well-formed version
  strictly greater than `__version__` (D10), not equal to `skipped_update_version`, and carrying
  both this build's platform asset and that asset's `.sig`. Any of these failing yields `None`.
  More than one asset matching the platform suffix also yields `None` — ambiguity fails safe.
  *Test:* `tests/test_update.py` — a table over newer / equal / older / `"v1.2-rc1"` / `"latest"`
  / `"1_0.0"` / `0.1` vs `0.1.0` / skipped / missing-`.sig` / duplicate-asset, each asserting the
  offer or its absence.
- **INV-6** A manual "Check for updates" action bypasses INV-1's opt-in gate **and nothing
  else** — the version comparison, the skip check and the asset predicate all still apply.
  *Test:* `tests/test_update.py` — `force=True` with the pref off calls the fetcher and offers;
  `force=True` on a skipped version still returns `None`.
- **INV-7** "Skip this version" persists to `.rolodex.conf` and suppresses exactly that version;
  a later, higher version is still offered. "Later" persists nothing.
  *Test:* `tests/test_update.py` — skip `1.4.0`, re-check against `1.4.0` → `None`; re-check
  against `1.4.1` → offered. Assert the config file gained the key and the vault did not.
- **INV-8** `download_and_verify` installs only bytes whose `.sig` verifies against the built-in
  public key. A one-byte change to either the payload or the signature raises
  `UpdateVerificationError`, and every temp file is removed.
  *Test:* `tests/test_update.py` — a throwaway test key is monkeypatched in and signs a fixture;
  the clean case returns a path, and payload-tamper and signature-tamper each raise with the temp
  directory left empty.
- **INV-9** Downloads are bounded: the asset by a byte cap, the API response and the signature by
  their own much smaller caps, and every request by a socket timeout. Exceeding a cap aborts and
  deletes the partial file. Only `https://` URLs are opened, and that is re-checked on **every**
  redirect hop rather than only the first.
  *Test:* `tests/test_update.py` — an over-cap stream raises and unlinks; `http://` and `file://`
  refuse; a redirect handler handed an `http://` target refuses before following.
- **INV-10** The temp is staged in the target binary's own directory, so the install is a
  same-filesystem `os.replace`. Any failure before the replace leaves the running binary
  byte-for-byte intact.
  *Test:* `tests/test_update.py` — assert the temp's parent equals `target_path().parent`; inject
  a raise before `os.replace` and assert the target's bytes are unchanged.
- **INV-11** With the placeholder all-zero public key in place, no signature verifies, so
  "Update now" always fails closed with `UpdateVerificationError` and never installs.
  *Test:* `tests/test_update.py` — sign a fixture with a real throwaway key, verify against the
  shipped placeholder constant, assert it raises.
- **INV-12** `urllib` is imported inside the fetch functions and never at module scope:
  `import rolodex` loads no network module.
  *Test:* `tests/test_update.py` — source scan of `rolodex.py` asserts no module-scope
  `urllib` / `socket` / `http` import, plus `import rolodex` in a clean interpreter leaves
  `urllib.request` absent from `sys.modules`.
- **INV-13** Any check-time failure — DNS, TLS, HTTP, malformed JSON, an unparseable version —
  yields `None` and is never surfaced as an error dialog. A failure during an explicitly
  requested **install** is surfaced, not swallowed: the user asked for that one.
  *Test:* `tests/test_update.py` — a fetcher raising each of those yields `None`; a verify
  failure inside `download_and_verify` propagates.
- **INV-14** The install swaps the binary and only then relaunches; if the relaunch spawn fails
  the process still exits, because the new binary is already in place and a manual restart gets
  the new version. It never returns into a running window whose binary was swapped underneath it.
  *Test:* `tests/test_update.py` — with `subprocess.Popen` and `os._exit` monkeypatched, assert
  the call order, and that a `Popen` raising `OSError` still reaches `os._exit`.
- **INV-15** The check and the download run off the GTK main thread, marshalling back with
  `GLib.idle_add`, matching the existing unlock/restore pattern. Locking or closing the window
  while a download is in flight tears the prompt down, and a download completing afterwards
  installs nothing and deletes its temp.
  *Test:* **none — GTK-layer behaviour, verified by hand.** See § 10.

## 6. Failure modes

| Failure | Behaviour | Invariant |
|---------|-----------|-----------|
| No network / DNS failure / TLS failure | Silent `None`; no dialog | INV-13 |
| GitHub rate-limits the check | Silent `None`; no dialog, no retry storm | INV-13 |
| Release has no `.sig` asset | No offer at all — never an unsigned install | INV-5 |
| Signature does not verify | `UpdateVerificationError`, surfaced; temps removed; binary untouched | INV-8, INV-10 |
| Download exceeds the byte cap | Aborted, partial file deleted | INV-9 |
| Server redirects to `http://` | Refused before the hop is followed | INV-9 |
| Target directory is read-only or full | `UpdateError`; running binary intact | INV-10 |
| Relaunch spawn fails after a successful swap | Process exits anyway; manual restart runs the new version | INV-14 |
| Vault is locked during the check | Check runs normally — it never touches the vault | INV-4 |
| App is closed mid-download | Prompt torn down; the completing download installs nothing | INV-15 |
| Signing key not yet generated | Every install fails closed; the check still works | INV-11 |

## 7. Tests

`tests/test_update.py`, new, following the existing flat `tests/test_*.py` pattern — Rolodex
does not use `tests/features/<name>/` directories, which is why `spec_lint`'s test-surface
checks cannot run against this spec (§ 10).

Every test uses `tmp_path` plus an **injected fake fetcher**; none touches the network, and none
uses the real signing key — a throwaway Ed25519 key is monkeypatched in where a valid signature
is needed. The pure-logic entry points (version parsing and comparison, asset selection,
signature verification, the config accessors) are GTK-free per `CLAUDE.md`, so they are testable
without a display.

The GTK layer (INV-15) has no automated coverage, and § 10 records that rather than claiming it.

## 8. Alternatives considered (and rejected)

**Unsigned download over HTTPS alone.** Rejected. TLS authenticates the host, not the artifact,
and it puts the whole security of the update path in GitHub account security and the CA system.
For a password manager the artifact must be independently verifiable. **This is the one decision
here that is not reversible later**: once users run a build whose verification is weak,
tightening it does not retroactively protect them.

**Reusing the vault's Fernet key.** Rejected outright — that is a symmetric key derived from the
user's master password. It cannot authenticate a publisher, and involving it in a network path
is exactly wrong.

**Auto-install without asking.** Rejected. A password manager replacing its own binary unattended
is a large amount of trust for a small amount of convenience, and it removes the user's chance to
read what changed.

**Bundling a full update framework.** Rejected — every candidate is heavier than the whole
application and would dominate a single-file app with one dependency.

**Making `certifi` a hard dependency.** Rejected in favour of D7's defensive import, which fixes
the frozen-binary case without changing what a source install needs.

**Doing Windows in this spec.** Rejected — see S4. Its swap cannot happen in process, so it is a
materially different mechanism, and it cannot be exercised from this machine at all.

## 9. Out of scope

- **Windows.** Deferred to its own roadmap item (S4).
- **Updating a source checkout** (`python3 rolodex.py`) or a distro/Flatpak package. Those are
  the packager's job; the feature stays inert there (INV-2).
- **Delta or partial updates.** The binary is replaced whole.
- **Downgrade or rollback.** Strictly-greater only (INV-5); recovery is re-downloading a release.
- **Background or scheduled checks.** The check runs at startup and on explicit request; there is
  no timer.
- **Amending `DESIGN.md`.** Required by § 2.1 and carries its own rule 14 gate.

## 10. What checks this

| INV | Checked by |
|-----|-----------|
| INV-1 | `tests/test_update.py` — fetcher call count is zero for absent / `false` / malformed |
| INV-2 | `tests/test_update.py` — `sys.frozen` unset → `detect_installer() is None` |
| INV-3 | `tests/test_update.py` — URL and header set asserted exactly |
| INV-4 | `tests/test_update.py` — check runs with no vault file present |
| INV-5 | `tests/test_update.py` — the ten-case offer table |
| INV-6 | `tests/test_update.py` — `force=True` bypasses only the opt-in gate |
| INV-7 | `tests/test_update.py` — skip round-trips through `.rolodex.conf` |
| INV-8 | `tests/test_update.py` — throwaway key; payload and signature tampers each raise |
| INV-9 | `tests/test_update.py` — cap abort, scheme refusal, redirect refusal |
| INV-10 | `tests/test_update.py` — temp parent asserted; pre-replace raise leaves target intact |
| INV-11 | `tests/test_update.py` — placeholder key rejects a validly-signed blob |
| INV-12 | `tests/test_update.py` — source scan plus `sys.modules` check |
| INV-13 | `tests/test_update.py` — raising fetcher yields `None`; verify failure propagates |
| INV-14 | `tests/test_update.py` — ordering with `Popen` / `os._exit` monkeypatched |
| INV-15 | **nothing — verified by hand** |
| S2, S3 | **nothing — assumptions recorded in § 3, not put to the user** |
| D9 relaunch, end to end | **nothing automated — empirical, needs two real signed releases** |

**Known gaps, stated rather than papered over:**

- `spec_lint` reported `surfaces_checked: false` against this spec, skipping
  `test_surface_absent`, `test_surface_unresolved` and `test_surface_unwired`. The verb resolves
  a test surface only in the `tests/features/<name>/` shape, which this project does not use. The
  `*Test:*` clauses above were therefore checked **by hand** against § 7, not by the verb, and
  every one describes a test that does not exist yet — this spec precedes its implementation.
- INV-15 has no automated coverage and is listed as such rather than being given a clause that
  would not run. The GTK layer is not under test in this project at all.
- **The signing key does not exist yet.** Until someone runs the keygen script and adds the
  private half as a repository secret, D4's fail-closed placeholder means the feature ships
  visible but non-functional at the install step. INV-11 pins that, but the **end-to-end path is
  unproven until a real signed release exists.**
- **macOS is built for `arm64` only.** An Intel Mac gets no matching asset and so no offer, which
  INV-5 makes safe but silent.

## 11. Cross-doc impact

| Document | Change owed |
|----------|-------------|
| `DESIGN.md` | Amend the first **Goal**'s "no network access of any kind" to carve out the opt-in check (§ 2.1). **Own rule 14 gate.** |
| `SECURITY.md` | Add the update path to the threat model: what signing protects against, what it does not, and the fail-closed placeholder |
| `README.md` | Document the preference and that it is off by default |
| `CLAUDE.md` | Record that `__version__` is now version-bearing, and the lazy-`urllib` confinement convention |
| `.claude/bump.json` | Add `rolodex.py`'s `__version__` to `files[]` and extend `post_check` |
| `.github/workflows/build.yml` | Sign each asset and attach the `.sig` |
| `requirements.txt` | **Unchanged** — `certifi` is build-time only (D7) |
| `docs/specs/README.md` | Add this spec to the table; it is the first non-retroactive spec there |

## 12. Cold-eyes loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Q4 | Outcome |
|------|------|-------|----|----|----|----|---------|
