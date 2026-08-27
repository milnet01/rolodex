# Spec: Opt-in Signed Auto-Update (ROLO-0037)

**Status:** accepted (2026-08-27) — gated by `review-contract`, 2 loops, 18 findings verified and fixed, cap reached (calm). Tail empty.

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

Rolodex publishes self-contained binaries on every `v*` tag
(`.github/workflows/build.yml`), and nothing tells a user a new one exists. They check GitHub,
or they never find out — so a security fix like 1.3.1's atomic vault write reaches only the
users who happen to look.

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
rather than ignoring it: the feature is off by default (INV-1), it is the app's only egress and
never transmits vault-derived data (INV-3), and `DESIGN.md` is amended in the same change.
Shipping code that contradicts the design document leaves two sources of truth, and the one
users read is the one nobody checked.

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
`build.yml` already fixes them, and the running build selects one by
`(sys.platform, platform.machine())`:

| `sys.platform` | `platform.machine()` | Asset |
|---|---|---|
| `linux` | `x86_64` | `rolodex-linux-x86_64` |
| `darwin` | `arm64` | `rolodex-macos-arm64` |
| `win32` | `AMD64` | *(reserved — see below)* |

**The `win32` row is reserved for the deferred Windows item and is not live** (S4). Windows is
out of scope here, and the mechanism this spec describes does not work there: INV-10 `os.replace`s
the running binary, which Windows refuses on a locked `.exe`, and D9 relaunches through
`/bin/sh`, which Windows does not have. INV-2 therefore refuses the platform outright, before any
offer.

**Any other pair yields no asset and therefore no offer** — an Intel Mac is the live case, and
matching on `sys.platform` alone would offer it the arm64 binary, which INV-10 would then
`os.replace` over a working install. The signature asset is that name + `.sig`. The Linux and
macOS assets carry no file extension, so the staged temp has none either — harmless, because
the file is `chmod +x`'d and `os.replace`d rather than opened by extension.

**D3 — Ed25519 detached signatures, verified against a key baked into the binary.**
`cryptography` is already Rolodex's one dependency and provides Ed25519, so this adds none. The
signature is the raw 64 bytes over the exact asset bytes, published as `<asset>.sig`.

**D4 — The private key never enters the repository.** `scripts/gen-signing-key.py` generates the
pair. The private half becomes a GitHub Actions secret and lives nowhere else. **The public half
is hand-pasted into `rolodex.py`'s constant and committed** — it is public by definition, and
baking it in at build time would mean the binary trusts whatever key the workflow happened to
hold, which is the property signing exists to remove. Both `scripts/gen-signing-key.py` and the
`build.yml` signing step consume the private key in the same encoding that script writes; it is
never transcribed by hand.

Until that key exists the public constant holds base64 of 32 zero bytes: the module imports
cleanly, key loading succeeds, and no real signature can verify — so the feature **fails closed**
in the interim rather than failing open (INV-11).

**D5 — Preferences live in `.rolodex.conf`, not the vault.** That file is already plaintext JSON
holding non-secret preferences (`idle_lock_seconds`, `clipboard_clear_seconds`). Two keys join
it: `check_for_updates` (bool, default `false`) and `skipped_update_version` (string). They must
be readable without the vault, because the check runs at startup while the app is locked
(INV-4); putting them in the vault would make the update check depend on the user having
unlocked, which is the wrong coupling.

**`load_config()` / `save_config()` take no argument today and read a module-level
`CONFIG_FILE`.** Either give them an optional path parameter or have the tests monkeypatch
`CONFIG_FILE` — the choice is the implementer's, but INV-4's and INV-7's tests need one of them,
so it cannot be left unnoticed until the tests are written.

**There is no preferences UI in Rolodex today**, so this feature must also add the surface that
sets `check_for_updates`. Without it the only way to opt in is hand-editing JSON, which is not an
opt-in a user can find. A menu entry beside the existing app-menu actions is enough; it is
disabled with a tooltip when `is_update_supported()` is `False` (INV-2).

**D6 — Network code is confined and lazily imported.** finbreak confines network access to one
module and greps for `import urllib` everywhere else. Rolodex is a single file, so that shape is
not available. Instead `urllib.request` is imported *inside* the fetch functions and never at
module scope, so `import rolodex` leaves `urllib.request` out of `sys.modules` and INV-12 asserts
exactly that name.

**It is not "no network stack", and the difference matters to whoever writes the test.**
`rolodex.py` already imports `urllib.parse` at module scope for TOTP `otpauth://` parsing, and
GTK and `cryptography` pull in `socket` and `ssl` transitively. All three are expected and none
may be removed. **Weaker than finbreak's whole-module ban**: it proves one import, not that a
future function adds no egress of its own.

**D7 — CA trust: `certifi` when present, the system store otherwise.** finbreak hit a real bug
here — a binary built on one distro, run on another whose CA bundle sits elsewhere, found no CAs
and the check failed silently. Rolodex has the same exposure, because its binaries are built on
`ubuntu-latest` and run anywhere. But a second hard dependency fights the one-dependency goal,
so `certifi` is a **build-time** dependency, bundled into the frozen binaries where the problem
exists, and imported defensively — a source checkout uses the system store, which is correct
there. `requirements.txt` is unchanged.

**D8 — `__version__` is introduced in `rolodex.py` and becomes a version-bearing file.**
`.claude/bump.json` gains a `files[]` entry that rewrites it, **and its `post_check` is
extended to assert `__version__` matches the CHANGELOG heading**. Both halves are needed:
`post_check` is a fixed shell string that greps `CHANGELOG.md` alone, so adding a `files[]`
entry does not extend it, and a `__version__` that failed to rewrite would pass the bump
silently. This is the prerequisite from § 2 and the one change that touches the release
recipe.

**D9 — The relaunch waits for the old process to exit.** A PyInstaller one-file binary unpacks
itself into a private `_MEI` directory and cleans it up on exit; spawning the replacement before
that teardown makes the new bootloader collide with the old extraction directory. finbreak
shipped this bug twice. The relaunch is therefore a detached `/bin/sh` that polls until the old
PID is gone — hard-capped, so a wedged process cannot hang it — then `exec`s the swapped binary
with `PYINSTALLER_RESET_ENVIRONMENT=1` and the loader variables restored from PyInstaller's own
`*_ORIG` values.

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
  call. **The same is true on `sys.platform == "win32"` whatever `sys.frozen` says** — Windows is
  deferred (S4) and this spec's install mechanism does not work there, so the platform is refused
  before any offer rather than after a 36 MB download. **Both gates live inside
  `check_for_update()`**, not in its GUI caller.
  *Test:* `tests/test_update.py` — with `sys.frozen` unset, assert all three and a zero fetcher
  call count; separately, with `sys.frozen` set and `sys.platform` forced to `win32`, assert the
  same. This is the **only** test that leaves the frozen state unset; see § 7.
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
  exactly one asset whose name **equals** this build's asset name from D2's table, plus exactly
  one whose name equals that name + `.sig`. Any of these failing yields `None`, as does a
  duplicate of either — ambiguity fails safe. **Matching is equality, never prefix or
  substring**: under those the required `.sig` is itself a second match, so the ambiguity guard
  would fire on every well-formed release and no update would ever be offered.
  *Test:* `tests/test_update.py` — a table over newer / equal / older / `"v1.2-rc1"` / `"latest"`
  / `"1_0.0"` / `0.1` vs `0.1.0` / skipped / missing-`.sig` / duplicate-asset, each asserting the
  offer or its absence.
- **INV-6** A manual "Check for updates" action bypasses INV-1's opt-in gate **and nothing
  else** — the version comparison, the skip check and the asset predicate all still apply.
  *Test:* `tests/test_update.py` — `force=True` with the pref off calls the fetcher and offers;
  `force=True` on a skipped version still returns `None`.
- **INV-7** "Skip this version" persists to `.rolodex.conf` and suppresses exactly that version;
  a later, higher version is still offered. "Later" persists nothing. `save_config` swallows
  `OSError` by design, so a skip that cannot be written is dropped silently and the version is
  offered again next launch — acceptable, and not to be "fixed" by making config writes fatal.
  *Test:* `tests/test_update.py` — skip `1.4.0`, re-check against `1.4.0` → `None`; re-check
  against `1.4.1` → offered. Assert the config file gained the key and the vault did not.
- **INV-8** `download_and_verify` installs only bytes whose `.sig` verifies against the built-in
  public key. A one-byte change to either the payload or the signature raises
  `UpdateVerificationError`, and every temp file is removed.
  *Test:* `tests/test_update.py` — a throwaway test key is monkeypatched in and signs a fixture;
  the clean case returns a path, and payload-tamper and signature-tamper each raise leaving **no
  staged temp beside the target binary**. Not "the directory is empty": INV-10 stages the temp in
  the target's own directory, which contains the target, so an empty-directory assertion can never
  pass.
- **INV-9** Downloads are bounded, and the bounds are these: asset 250 MB, API response 1 MB,
  signature 4 KB, socket timeout 30 s. Exceeding a cap aborts and deletes the partial file.
  Only `https://` URLs are opened, and that is re-checked on **every** redirect hop rather than
  only the first. The asset cap is a floor sized with headroom over what `build.yml` currently
  produces — re-derive with `gh release view <tag> --json assets -q '.assets[].size'` before
  lowering it, because a cap under the real artifact aborts every genuine update while a
  synthetic over-cap test still passes.
  *Test:* `tests/test_update.py` — an over-cap stream raises and unlinks; `http://` and `file://`
  refuse; a redirect handler handed an `http://` target refuses before following.
- **INV-10** The temp is staged in the target binary's own directory, so the install is a
  same-filesystem `os.replace`. Any failure before the replace leaves the running binary
  byte-for-byte intact.
  *Test:* `tests/test_update.py` — assert the temp's parent equals `target_path().parent`; inject
  a raise before `os.replace` and assert the target's bytes are unchanged.
- **INV-11** With the placeholder all-zero public key in place, no signature verifies, so
  "Update now" always fails closed with `UpdateVerificationError` and never installs.
  *Test:* `tests/test_update.py` — assert the shipped constant decodes to 32 zero bytes, **and**
  that a fixture signed with a throwaway key fails against it. The first half is the one that
  works: a throwaway key's signature fails against *any* other key, so the second half alone stays
  green whether the placeholder or a real production key is shipped, and would never detect that
  the key had — or had not — been replaced. The test is meant to fail the day a real key lands, so
  that INV-11 is retired in that same commit.
- **INV-12** `urllib.request` is imported inside the fetch functions and never at module scope, so
  `import rolodex` does not load it.
  *Test:* `tests/test_update.py` — source scan of `rolodex.py` asserts no module-scope
  `urllib.request` / `urllib.error` / `socket` / `http` import, plus `import rolodex` in a clean
  interpreter leaves `urllib.request` absent from `sys.modules`.
  **Scan for those names and no wider one, and exempt `urllib.parse`**: `rolodex.py` imports it at
  module scope today for TOTP `otpauth://` parsing, so a scan for bare `urllib` fails on correct
  pre-existing code — and the cheapest way to make it pass is to delete that import, which breaks
  TOTP field parsing. Likewise assert `urllib.request` and no wider name in `sys.modules`:
  `urllib`, `socket` and `ssl` are all present after import, via that same `urllib.parse` and via
  GTK and `cryptography`.
- **INV-13** Any check-time failure — DNS, TLS, HTTP, malformed JSON, an unparseable version —
  yields `None` on the silent startup path and is never surfaced as an error dialog. A failure
  during an explicitly requested **install** is surfaced, not swallowed: the user asked for that
  one. **On a forced check (INV-6) the caller must be able to tell "up to date" from "could not
  check"** — `None` alone conflates them, and a manual button that answers a DNS failure with
  "You're up to date" is wrong. The forced path therefore reports the failure to its caller, which
  shows "Couldn't check for updates" rather than an error dialog.
  *Test:* `tests/test_update.py` — a fetcher raising each of those yields `None` unforced; the
  same fetcher under `force=True` yields a distinguishable failure rather than `None`; a verify
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
| Running on Windows | Refused before any offer — the platform is out of scope (S4) | INV-2 |
| Forced check cannot reach the network | Reported as "couldn't check", never as "up to date" | INV-13 |

`UpdateVerificationError` (a signature that did not verify) and `UpdateError` (everything else
that went wrong during an install — staging, oversize, timeout, a failed swap) are the two the
GUI catches. Both derive from one base so a caller may catch either separately or both together.

## 7. Tests

`tests/test_update.py`, new, following the existing flat `tests/test_*.py` pattern — Rolodex
does not use `tests/features/<name>/` directories, which is why `spec_lint`'s test-surface
checks cannot run against this spec (§ 10).

Every test uses `tmp_path` plus an **injected fake fetcher**; none touches the network, and none
uses the real signing key — a throwaway Ed25519 key is monkeypatched in where a valid signature
is needed.

**A pytest process is never frozen, so every test that expects an offer must fake one**:
monkeypatch `sys.frozen` and `sys.executable` to simulate a frozen build. Without this INV-1,
INV-4, INV-5 and INV-6 all assert an offer that INV-2's gate correctly refuses, and the
cheapest way to make them pass is to weaken that gate — which is the one change this spec
must not invite. The pure-logic entry points (version parsing and comparison, asset selection,
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
- **Running `DESIGN.md`'s rule 14 gate under this spec's gate.** The amendment itself ships
  *in this change* and is not optional (§ 2.1); only its own gate is out of scope here.

## 10. What checks this

| INV | Checked by |
|-----|-----------|
| INV-1 | `tests/test_update.py` — fetcher call count is zero for absent / `false` / malformed |
| INV-2 | `tests/test_update.py` — `sys.frozen` unset → `detect_installer() is None` |
| INV-3 | `tests/test_update.py` — URL and header set asserted exactly |
| INV-4 | `tests/test_update.py` — check runs with no vault file present |
| INV-5 | `tests/test_update.py` — the offer table |
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
| `README.md` | Document the preference, where to set it, and that it is off by default |
| `CLAUDE.md` | Record that `__version__` is now version-bearing, and that `urllib.request` is imported lazily inside the fetch functions while the module-scope `urllib.parse` is expected |
| `.claude/bump.json` | Add `rolodex.py`'s `__version__` to `files[]` and extend `post_check` |
| `.github/workflows/build.yml` | Sign each asset and attach its `.sig`; **add `certifi` to the build-time pip install** so PyInstaller bundles it (D7) |
| `requirements.txt` | **Unchanged** — `certifi` is build-time only (D7) |
| `docs/specs/README.md` | Add this spec to the table **and qualify its opening sentence**, which currently says every entry is retroactive |

## 12. Cold-eyes loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Q4 | Outcome |
|------|------|-------|----|----|----|----|---------|
| 1 | 2026-08-27 | 3, cold — genre pinned `spec` | 2 | 3 | 3 | 1 | **Nine verified, nine fixed, none dismissed.** Three defects were found by more than one lane. All three found the same one, and it is the worst: § 2.1 rested the whole resolution of the `DESIGN.md` conflict on the amendment landing *in this change*, while § 9 listed that amendment as out of scope — so an implementer could ship the feature with the design document still saying "no network access of any kind", which is precisely the state § 2.1 forbids. Two lanes each found the missing `certifi` build step (§ 11 told an implementer to change `build.yml` for signing only, so the frozen binary would ship without the CA bundle D7 depends on and the check would fail silently) and the `docs/specs/README.md` row (its opening sentence claims every entry is retroactive, which this spec is not). **The sharpest single-lane finding was the asset predicate**: "suffix" admits prefix matching, under which a release's own required `.sig` is a second match, so the ambiguity guard would fire on every well-formed release and no update would ever be offered — while a synthetic duplicate-asset test still passed. The same lane found that nothing mapped a running process to an asset name at all, which would have offered an Intel Mac the arm64 binary and then `os.replace`d it over a working install. **One Q1 was a false claim about this project's own tooling**: D8 said adding a `files[]` entry to `.claude/bump.json` makes `post_check` enforce `__version__` lockstep, and `post_check` is a fixed shell string that greps `CHANGELOG.md` alone. Three lane open questions were settled by running them rather than reasoning: the all-zero placeholder key loads and rejects 100 real signatures plus crafted low-order forgeries, so it fails closed as D4 claims; `PYINSTALLER_RESET_ENVIRONMENT` and `LD_LIBRARY_PATH_ORIG` are both present in the shipped bootloader binary, so D9 holds; and `import rolodex` does leave `urllib.request` absent, though `urllib`, `socket` and `ssl` all arrive transitively via GTK and `cryptography` — INV-12 was true as written but a slightly broader assertion would fail, so it now says which name to assert. None of those three is counted above. |
| 2 | 2026-08-27 | 3, cold — identical brief, packet rebuilt from disk | 2 | 2 | 4 | 1 | **Nine verified, nine fixed, none dismissed. Cap reached (2 for a spec); the run files its tail and ships.** All three lanes independently found the same defect, and it was loop 1's own collateral: loop 1 added D2's platform table to fix an undefined mapping, and the table carried a live `win32` row while § 3 and § 9 both defer Windows — with no invariant gating on platform, since INV-2 gated only on `sys.frozen`, which a frozen Windows build sets. A Windows user would have been offered an update, downloaded it, verified it, and then hit an `os.replace` over a locked `.exe` and a `/bin/sh` relaunch on a platform with no `/bin/sh`. **The most valuable finding corrected a fact this orchestrator had asserted to all six lanes as settled**: the packet said `urllib` reaches `sys.modules` transitively, and one lane opened the source and found `import urllib.parse` at module scope, used for TOTP `otpauth://` parsing. INV-12's test as written scanned for bare `urllib`, so it would have failed on correct pre-existing code — and the cheapest way to make it pass is deleting that import, which breaks TOTP. Never assert a source fact to a lane without opening the file. **One Q4 was a test that could not fail**: INV-11 verified a throwaway-key signature against the shipped placeholder, and a throwaway key's signature fails against *any* other key, so the assertion stayed green whether the placeholder or a real production key shipped — falsifying nothing about the claim it existed to pin. It now asserts the constant decodes to 32 zero bytes, so it fails deliberately the day a real key lands. Also fixed: D6 still said `import rolodex` pulls in no network stack, which loop 1's own INV-12 fix had just contradicted; INV-8 asserted an empty temp directory that INV-10 guarantees can never be empty, since the temp is staged beside the target binary; the public key's route into the baked-in constant was never stated, so signing could be set up completely and still fail closed forever; and INV-13 collapsed "up to date" and "could not check" into one `None` while INV-6 promised a manual check button. **One finding was the orchestrator's, found while reading the source for the implementation and missed by all six lanes across both loops: the spec described an opt-in feature with no way to opt in.** Rolodex has no preferences UI at all, so as written the only route was hand-editing JSON. **Calm cap:** two of this loop's nine findings landed on text loop 1 wrote; the rest were defects the document had held from drafting. The document held more defects than the cap held loops, so shipping is right and the tail is empty — every finding was fixed. |
