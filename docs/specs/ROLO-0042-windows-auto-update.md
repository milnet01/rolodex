<!-- ants-spec-format: 1 -->
# ROLO-0042 — Extend the signed in-app update to Windows

Genre: spec
Status: active
Describes: planned

**Status:** accepted (2026-11-11).
**Kind:** feature.
**Source:** ROADMAP ROLO-0042 (in-session 2026-08-27; deferred from ROLO-0037 scope decision S4).
**Pairs with:** `docs/specs/ROLO-0037-auto-update.md` (the updater this extends).

**Layman:** On Windows, "Update now" will install the new version and reopen Rolodex, as it
already does on Linux and macOS; your vault and settings are not touched.

## 1. Goal

A frozen Windows build of Rolodex offers, verifies, installs and relaunches a newer signed
release, with the same Ed25519 gate as Linux and macOS. No failure leaves the user without a
working `.exe`. The whole path is proven on a real Windows PC by an agent, with no person at
either machine.

## 2. Problem

`PLATFORM_ASSETS` in `rolodex.py` has no `win32` key, so `platform_asset_name()` returns `None`,
`is_update_supported()` is `False`, and `check_for_update()` returns before any request
(ROLO-0037 INV-2). Windows users get no update, though every release already ships
`rolodex-windows-x86_64.exe` and its `.sig` (`gh release view v2.0.0`).

Turning the key on is not enough:

1. `apply_update()` calls `os.replace(new_file, target)` on the running binary. Windows refuses
   to overwrite a running `.exe`.
2. The relaunch is `_relaunch_command()`, a `/bin/sh` waiter. Windows has no `/bin/sh`.
3. The Windows swap must happen after Rolodex exits, so it happens out of process. That opens a
   gap between the signature check and the swap which the in-process Linux path does not have.
4. No one can run the Windows build by hand. Shipped builds trust only the release key, whose
   private half is a GitHub secret, so a test needs signed releases from somewhere else.

## 3. Scope decisions (agreed with the user)

| # | Decision | Who |
|---|----------|-----|
| S1 | Follow finbreak FIBR-0131: a PowerShell helper, waiting on the image path. Departs in one place, the swap itself (S5) | Brief, 2026-11-11 |
| S2 | Live only on `(win32, AMD64)`; native ARM64 Windows stays absent | Brief, 2026-11-11 |
| S3 | An agent verifies on the Windows test PC over `ssh wintest`; the maintainer runs nothing | Brief, 2026-11-11 |
| S4 | Test releases come from **test builds**: same source, with a test key, a test feed and a test version written in at build time (§ 4.6). No runtime override exists | Drafting agent; open question Q1 |
| S5 | The swap renames the old `.exe` aside, then renames the new one in, never `Move-Item -Force` (§ 4.3) | Drafting agent, from a measurement |

## 4. Design

### 4.1 Platform row and build gate

`PLATFORM_ASSETS` gains `("win32", "AMD64"): "rolodex-windows-x86_64.exe"`, the asset name in
`build.yml`'s matrix. Matching stays equality (ROLO-0037 INV-5). An x64 build running under
emulation on ARM64 Windows reports `AMD64` and gets the x64 asset, which is what it is running.

`build.yml` builds Windows with MSYS2 UCRT64 Python. Nobody has checked what that Python
returns for `platform.machine()`. So `--selftest` on a frozen build now exits non-zero
when `platform_asset_name()` is `None`. Every CI build then proves its own key before a
release ships an `.exe` that would never be offered an update.

### 4.2 Staging and the hand-off

`download_and_verify()` is unchanged in what it checks: the same Ed25519 verify over the
same bytes, on every platform. It now returns `StagedUpdate(path, sha256)`. `sha256` is the hex
digest of the buffer it just verified, not a re-read of the file. The Linux and macOS
branch of `apply_update()` uses only `.path`.

On `sys.platform == "win32"`, `apply_update()` calls `_apply_update_windows()`, which:

1. opens `<exe dir>\.rolodex-update-helper.log` for writing, truncated (falls back to
   `DEVNULL` if it cannot);
2. spawns the helper with `subprocess.Popen(_windows_helper_command(...),
   env=_windows_helper_env(), creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
   close_fds=True, stdin=DEVNULL, stdout=<log>, stderr=STDOUT)`. The flags are read with
   `getattr(subprocess, name, 0)`, so the code imports on Linux. **Never `DETACHED_PROCESS`**:
   finbreak measured that PowerShell started with it dies before its first line (FIBR-0444).
3. If `Popen` raises `OSError`: unlink the staged file and raise `UpdateError`. The secrets are
   not wiped and the window stays usable.
4. Otherwise call `on_before_exec` (`_wipe_secrets_for_update`), then `os._exit(0)`.

No `chmod` and no `os.replace` runs in process on Windows. The staged file is created by
`mkstemp` in the `.exe`'s own folder, as on Linux (ROLO-0037 INV-10). That folder must
accept a new file, so a read-only or protected folder fails at download time, before
the hand-off.

### 4.3 The helper

`_windows_helper_command(exe, new_file, sha256)` returns
`[<powershell>, "-NoProfile", "-NonInteractive", "-Command", <script>]`. `<powershell>` is
`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` when that file exists, else
`shutil.which("powershell")`, else `"powershell.exe"`. Paths are joined with `ntpath`, so the
Linux tests, which pass Windows-form paths, see `\` separators. They are single-quoted with
embedded `'` doubled. `-Command` is not subject to the script execution policy and leaves
**no helper script file** to clean up or to be edited during the wait. The script, laid out:

```powershell
$exe = '<target>'; $new = '<staged>'; $old = '<exe dir>\.rolodex-update-old'; $want = '<sha256>'
function Busy { Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } }
function Drop { Remove-Item -LiteralPath $new -Force -ErrorAction SilentlyContinue }
function Try5($a, $b) { for ($j = 0; $j -lt 5; $j++) {
    try { Move-Item -LiteralPath $a -Destination $b -ErrorAction Stop; return $true }
    catch { Start-Sleep -Milliseconds 500 } }; return $false }
for ($i = 0; $i -lt 300; $i++) { if (-not (Busy)) { break }; Start-Sleep -Milliseconds 200 }
if (Busy) { 'abort: image still running'; Drop; exit 1 }                  # no relaunch
if ((Get-FileHash -LiteralPath $new -Algorithm SHA256).Hash -ne $want) {
    'abort: staged bytes changed'; Drop; Start-Process -FilePath $exe; exit 1 }
if (Test-Path -LiteralPath $exe) { Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue }
if (-not (Try5 $exe $old)) { 'abort: old binary would not move'; Drop; Start-Process -FilePath $exe; exit 1 }
if (Try5 $new $exe) { Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue; 'swapped' }
elseif (Try5 $old $exe) { 'restored'; Drop }
else { 'RESTORE FAILED: the old binary is at ' + $old }
Start-Process -FilePath $exe
```

**The wait is on the image path, not a PID** (FIBR-0131 D3). A one-file build is a bootloader
plus a child process, both running the `.exe`, and Windows reuses PIDs. The poll ends only when
neither runs, which is also when the bootloader has removed its `_MEI` folder. Its cap is
60 s. PowerShell's `-eq` ignores case, as Windows paths do.

**Why not FIBR-0131's `Move-Item -Force`.** Measured 2026-11-11 on `ssh wintest`
(PowerShell 5.1.19041): with the source held open, `Move-Item -Force src dst` raised
`IOException`, and `dst` no longer existed. It deletes the destination first. Every step above
is instead a plain rename within one folder, which either happens whole or not at all. At
every moment the original bytes are at `$exe` or at `$old`, and no partly written `.exe` can
exist. A leftover `$old` is deleted only while `$exe` exists: after a failed restore it is the
only copy (finbreak 3679b21 guards the same case).

**Between the signature check and the swap** the staged file sits in the `.exe`'s folder for
up to the wait cap. The helper re-hashes it just before the swap and refuses a mismatch.
What is left is the moment between the re-hash and the rename. Only a process that can write
to that folder can use it, and such a process can already replace the `.exe` directly. On
Windows `mkstemp`'s `0o600` sets no ACL; the file inherits the folder's, so it is exactly as
protected as the `.exe` beside it. This is a guard against accidental change, not a new
privilege boundary.

### 4.4 Relaunch

`Start-Process -FilePath $exe` replaces D9's `/bin/sh`. `_windows_helper_env()` is
`dict(os.environ)` plus `PYINSTALLER_RESET_ENVIRONMENT=1`. `Start-Process` passes the helper's
environment on, so the reset flag reaches the new bootloader. It touches no `LD_*` variable:
those are POSIX-only. PyInstaller's GTK hooks may leave the old `_MEI` path in variables that the
new process inherits. Cycle 2 of § 4.7 exercises that, because its updating instance is one
the helper launched.

The helper relaunches after a swap, a restore and a hash mismatch: in each case the image is
free and an `.exe` sits at `$exe`. It does not relaunch on a timeout, because another instance
is still running that image.

### 4.5 What the update touches

The helper names three paths, `$exe`, `$new` and `$old`; the app opens its log. All four are in
the `.exe`'s folder.
The vault and `.rolodex.conf` live in `APP_DIR` (`GLib.get_user_data_dir()`\\`Rolodex`), which no
step names. The old process's `VaultLock` on `<vault>.lock` is released when it exits, before
the relaunch can take it.

The helper log and `.rolodex-update-old` start with `.rolodex-update-`. So the existing
`sweep_stale_update_temps()` removes them, as it does any other staged leftover.

### 4.6 Test releases without a runtime override

A shipped binary trusts `RELEASE_PUBLIC_KEY_B64` and fetches from `GITHUB_OWNER/GITHUB_REPO`,
both literals. Anything that let an environment variable, flag or config key change either
would let whoever controls it install code: it is remote code execution, and none is added.

**Test builds instead.** `scripts/make-update-test-source.py SRC OUT --key B64 --feed
OWNER/REPO --version V` writes a copy of `rolodex.py` with four literals replaced:
`__version__`, `RELEASE_PUBLIC_KEY_B64`, `GITHUB_OWNER` and `GITHUB_REPO`. It refuses when
any of these holds:

- one of the four assignment lines (`NAME = "…"` at module scope) does not occur exactly once;
- `--key` equals the committed key;
- `--feed` is `milnet01/rolodex`;
- `--version` fails `parse_version`.

`.github/workflows/update-test-build.yml` runs on `workflow_dispatch` only. It has
`permissions: contents: read` and no `environment:`.
Over a matrix of versions it writes the test source over `rolodex.py` in its own checkout, runs
`packaging/windows-build.sh`, and uploads each `.exe` as an artifact. It publishes nothing. Its
MSYS2 setup step and its `pip install` line are copies of those in `build.yml`'s Windows job.

**The test key never leaves this machine.** Each run makes a fresh pair with
`scripts/gen-signing-key.py` in `/mnt/Emulators/.claude/Scratchpad/Rolodex/update-e2e/`. Only the
public half goes to the workflow, as an input. The `.sig` files are made locally, and the pair is
deleted when the run ends.

**The feed** is a public repository, `milnet01/rolodex-update-test` (Q1), whose releases the
run creates and deletes. It must be public: `/releases/latest` on a private repository needs a
token, and ROLO-0037 INV-3 forbids sending one.

This is the same tension ROLO-0037 D4 settled for releases: a key baked in at build time is
trusted by whatever binary the workflow built. That is harmless here because these binaries
never reach a release of `milnet01/rolodex`. They report a `0.0.x` version and trust only a
key that is deleted after the run. The update path they exercise is the shipped code; the four
literals are all that differ.

### 4.7 Verification on the Windows test PC

Everything runs from Linux. The facts it relies on come from the shared know-how entries
`windows-test-machine` and `wintest-drive-qt-app-ui-automation`:

- `ssh wintest` lands in cmd.exe, with home `C:\Users\aants`.
- PowerShell goes in a copied `.ps1` file run with `-File`.
- GUI programs must start in the signed-in session through one `schtasks /it` task, which
  runs a runner `.ps1` that performs a job `.ps1` copied in for each step and writes a log
  that ends with `DONE`.

The Qt UI Automation route in the second entry is unverified for GTK 4, so the runner sends
keystrokes with `SendInput`.

**`scripts/wintest-helper-probe.sh`** tests the helper with no GUI. It writes
`_windows_helper_command()` output for each leg to JSON on Linux, copies it over, and runs it
through a copied `.py` file under `wintest`'s own Python, with § 4.2's creation flags. The
running "image" is a copy of `C:\Windows\System32\PING.EXE` started with `-n <seconds>
127.0.0.1`. Legs:

- **H1** — image exits after 5 s: swapped, `$old` gone, log ends `swapped`.
- **H2** — image runs past the cap: target unchanged, staged gone, no relaunch.
- **H3** — the probe holds `$new` open without delete sharing: target holds the original
  bytes, log says `restored`.
- **H4** — wrong digest: target unchanged, staged gone, relaunch attempted.
- **H5** — H1 with a space and an apostrophe in the folder name.

**`scripts/wintest-update-e2e.sh`** is the two-cycle test. Exit 0 is PASS, 1 is FAIL, and 2 is
BLOCKED, meaning a precondition failed and nothing was tested.

0. **Preconditions** (BLOCKED otherwise): `ssh -o BatchMode=yes wintest ver` answers; `LogonUI`
   is not running; no `rolodex*` process is running; neither `%APPDATA%\Rolodex` nor
   `%LOCALAPPDATA%\Rolodex` exists. The last guard keeps any real vault safe, because the test
   uses the real data folder (step 3).
1. Make the test key; dispatch `update-test-build.yml` for versions `0.0.1`, `0.0.2` and
   `0.0.3` (A, B, C); `gh run watch`; `gh run download`. Sign B and C locally.
2. Copy A to `C:\Users\aants\rolodex-e2e\app\rolodex-windows-x86_64.exe`.
3. Run `A --selftest` over ssh. A frozen build creates `APP_DIR` on import, so whichever
   data folder now exists is `APP_DIR`; record it. Write into it a vault made on Linux with
   `create_vault()`, holding made-up entries and a random alphanumeric password, plus
   `.rolodex.conf` = `{"check_for_updates": true}`. Record the vault's SHA-256.
4. **Cycle 1.** Publish B as the latest release on the feed; poll the API until
   `tag_name` is `v0.0.2`. The runner opens A the way a double-click does (Explorer
   `ShellExecute`, per the know-how), focuses its window, types the password and Enter. It
   then sends the fixed keys that pick **Update Now**; the first run settles that key sequence
   and the script records it.
5. **Check, from Linux, within 120 s:**
   - the `.exe`'s SHA-256 equals B's;
   - a process whose `Path` is the `.exe` started after step 4;
   - no `.rolodex-update-*` file is left except the log, whose last line is `swapped`;
   - the vault's SHA-256 is unchanged;
   - `.rolodex.conf` still has `check_for_updates: true` and no `skipped_update_version`.

   A wrong key press shows up as a timeout (Later) or a skip key (Skip). Only on a failure, the
   runner takes one screenshot.
6. **Cycle 2.** Publish C. Drive the instance the helper launched in cycle 1: type the
   password, pick Update Now, and run step 5's checks against C. This proves that an instance
   the helper started, with the reset flag and inherited environment, can itself update.
7. **Clean up, always:** stop Rolodex; delete `rolodex-e2e`, the data folder this run
   created, both feed releases with their tags, the scheduled task and the scratch key pair.

## 5. Invariants

- **INV-1** — `platform_asset_name()` is `"rolodex-windows-x86_64.exe"` for exactly
  `("win32", "AMD64")`. For `("win32", "ARM64")` and any other Windows machine it is `None`, and
  `check_for_update()` makes no request. A release carrying the asset, its `.sig` and a
  `rolodex-windows-x86_64.exe.zip` is offered. *Test:* `tests/test_update_windows.py::test_win32_asset_row`
  → passes. *Breaks when:* matching becomes a prefix test (the `.sig` and `.zip` become
  second matches and the guard refuses), or an ARM64 row is added.
- **INV-2** — A frozen `--selftest` exits non-zero when `platform_asset_name()` is `None`.
  *Test:* `tests/test_update_windows.py::test_selftest_fails_without_asset`, with a fake
  `sys._MEIPASS` whose `gi_typelibs` holds every `BUNDLED_TYPELIBS` file → exit 0 with the asset
  row and non-zero without it; plus the
  Windows job of `build.yml`, which runs `--selftest` and fails the build on a non-zero exit.
  *Breaks when:* MSYS2 Python reports a machine other than `AMD64`, or the check is removed.
- **INV-3** — The Windows install receives only what `download_and_verify()` returned. That
  function verifies with the same code on every platform, and its `sha256` is the digest of
  the verified buffer. *Test:* `tests/test_update_windows.py::test_staged_digest_and_tamper`
  → with `sys.platform` forced to `win32`, the clean fixture's `StagedUpdate.sha256` equals its
  SHA-256; a one-byte payload or signature tamper raises `UpdateVerificationError` and leaves
  no staged file. *Breaks when:* the digest covers the `.sig` or other bytes, or a Windows
  branch skips the verify.
- **INV-4** — On `win32`, `apply_update()` performs no in-process `os.replace` or `chmod`. It
  spawns, then wipes, then exits, with `creationflags` equal to `CREATE_NO_WINDOW |
  CREATE_NEW_PROCESS_GROUP` and the `DETACHED_PROCESS` bit clear. A `Popen` `OSError` unlinks
  the staged file, raises `UpdateError` and never calls the wipe. *Test:*
  `tests/test_update_windows.py::test_apply_windows_order_and_flags` → passes, with `Popen`,
  `os._exit`, `os.replace` and `os.chmod` monkeypatched, neither of the last two called, and the
  three flags set to `0x1`, `0x2` and `0x4`.
  *Breaks when:* the wipe moves before the spawn, `DETACHED_PROCESS` returns, or the Linux
  branch runs on Windows.
- **INV-5** — The helper waits until no process's `Path` equals the target, capped at 60 s.
  If the image is still running at the cap, it deletes the staged file and neither swaps nor
  relaunches. *Test:* `tests/test_update_windows.py::test_helper_script_shape` (the poll
  filters on `$_.Path -eq $exe`, uses no PID, and the abort branch has `Drop` and no
  `Start-Process`); `scripts/wintest-helper-probe.sh` legs H1 and H2 → `PASS`. *Breaks when:*
  the wait switches to a PID, or the abort branch relaunches.
- **INV-6** — The swap never leaves a missing or partly written `.exe`: the original bytes are
  always at the target or at `.rolodex-update-old`. The script never uses `-Force` on a
  `Move-Item`. *Test:* `test_helper_script_shape` (no `-Force` on any `Move-Item`; the `Try5`
  calls run old-aside, new-in, restore, in that order); `scripts/wintest-helper-probe.sh` leg H3 → `PASS`.
  *Breaks when:* `Move-Item -Force` returns (measured: it deletes the destination first), or
  the restore branch is dropped.
- **INV-7** — Immediately before the swap, the helper re-hashes the staged file. On a
  mismatch it deletes the file, swaps nothing and relaunches the old `.exe`. *Test:*
  `test_helper_script_shape` (`Get-FileHash` precedes the first `Try5` call);
  `scripts/wintest-helper-probe.sh` leg H4 → `PASS`. *Breaks when:* the hash check moves after
  the swap, or compares against a digest of the file re-read after verification.
- **INV-8** — The relaunch is `Start-Process -FilePath $exe`, with no `/bin/sh`. The helper
  environment is `os.environ` plus `PYINSTALLER_RESET_ENVIRONMENT=1`, and nothing else is
  changed. *Test:* `tests/test_update_windows.py::test_helper_env` → passes;
  `scripts/wintest-update-e2e.sh` cycle 2 → `PASS cycle 2`. *Breaks when:* the reset flag is
  dropped, which makes the relaunched bootloader collide with the old `_MEI` folder.
- **INV-9** — The literals assigned to `$exe`, `$new` and `$old` are the target, the staged
  file and `.rolodex-update-old`, all in the target's folder. No other literal in the script
  contains `\`. The vault's bytes are unchanged by an update. *Test:* `test_helper_script_shape`
  (the three assignments and the backslash rule); `scripts/wintest-update-e2e.sh` → vault SHA-256 equal across both cycles. *Breaks
  when:* the helper gains a path in `APP_DIR`, or an update path writes the vault.
- **INV-10** — Production builds carry no key or feed override. `RELEASE_PUBLIC_KEY_B64`,
  `GITHUB_OWNER` and `GITHUB_REPO` are each assigned once, at module scope, to a string
  literal. The SHA-256 of the key literal and the value `milnet01/rolodex` are pinned. The bodies of
  `release_public_key`, `fetch_latest_release`, `_require_update_url` and
  `download_and_verify` read no `os.environ`, `os.getenv`, `sys.argv` or `load_config`. No
  reference to `fetch_latest_release` passes an owner or repo, and no call of
  `check_for_update` outside tests passes `fetcher`.
  *Test:* `tests/test_update_windows.py::test_no_key_or_feed_override` → passes. *Breaks when:*
  someone adds an override, or commits a test key or feed into `rolodex.py`.
- **INV-11** — Test builds stay apart from releases. `make-update-test-source.py` refuses
  each case in § 4.6. `update-test-build.yml` has no tag trigger, no `contents: write`, no
  `environment:` and no `ROLODEX_SIGNING_KEY`. Its MSYS2 step and `pip install` line equal those
  of `build.yml`'s Windows job. `build.yml` never names the generator. *Test:*
  `tests/test_update_test_build.py` → passes. *Breaks when:* the test workflow gains the
  secret or a publish step, or the release workflow starts using the generator.
- **INV-12** — A Windows update, from "Update Now" to the new version running, completes
  unattended twice in a row. The second update is started by an instance that the first
  update's helper launched. *Test:* `scripts/wintest-update-e2e.sh` → exit 0, printing
  `PASS cycle 1` and `PASS cycle 2`. *Breaks when:* the helper dies with an Explorer-launched
  parent, the image wait returns early, or the relaunch environment breaks the next instance.

## 6. Failure modes

| Failure | Behaviour | Invariant |
|---------|-----------|-----------|
| Native ARM64 Windows | No asset, so no offer and no request | INV-1 |
| MSYS2 Python reports an unexpected machine | The CI self-test fails, so no release ships an `.exe` that can never update | INV-2 |
| Signature or payload tampered | `UpdateVerificationError` before any hand-off; nothing staged | INV-3 |
| Antivirus or AppLocker blocks `powershell.exe` | `UpdateError` shown; secrets kept; staged file deleted; window stays usable | INV-4 |
| Another Rolodex is running from the same `.exe` | Wait reaches the cap; staged file deleted; no relaunch; the other instance carries on and the offer returns next launch | INV-5 |
| Antivirus holds the new file | Rename retried, then the original restored and relaunched; the log says `restored` | INV-6 |
| Staged bytes change during the wait | Abort, staged file deleted, old `.exe` relaunched | INV-7 |
| `.exe` folder read-only or protected (e.g. `Program Files`) | `mkstemp` fails at download time, before the hand-off; `UpdateError` | ROLO-0037 INV-10 |
| `.exe` itself protected though its folder is writable | The rename aside fails; abort; old `.exe` relaunched | INV-6 |
| Power loss between the two renames | No `.exe` at the target; the original is at `.rolodex-update-old` and can be renamed back by hand. Accepted | INV-6 |
| Restore also fails | Log line `RESTORE FAILED` names `.rolodex-update-old`; recovery is by hand | INV-6 |
| Swap fails after the hand-off | Silent in the app: the old version reopens and offers again. The helper log is the record. "Skip This Version" ends the loop | INV-6 |
| Helper killed mid-wait (sign-out, shutdown) | `.exe` untouched; staged file swept a day later | INV-9 |
| SmartScreen or Defender distrusts the new unsigned `.exe` | Outside this mechanism: the same warning a first manual download shows | § 9 |
| Windows build 2.0.0 or older | Refuses Windows itself (ROLO-0037 INV-2), so its users install the first release with this feature by hand | § 11 |

## 7. Tests

**Pure tests, on Linux and in CI**, follow the flat `tests/test_*.py` pattern:
`tests/test_update_windows.py` (INV-1, INV-2, INV-3, INV-4, INV-8, INV-9, INV-10) and
`tests/test_update_test_build.py` (INV-11). Like ROLO-0037 § 7, each one fakes a frozen
build by monkeypatching `sys.frozen`, `sys.executable`, `sys.platform` and `platform.machine`.
Each new test must be seen failing against the code before its change.
`test_helper_script_shape` asserts structure and names, not the tuning values (300 × 200 ms,
5 × 500 ms), which the probe may change.

**Changed:** ROLO-0037's INV-2 test forced `win32` and expected a refusal. It now forces
`("win32", "ARM64")`. Callers of `download_and_verify()` move to `StagedUpdate`.

**Windows, driven from Linux** (not CI, which has no LAN PC):

- `scripts/wintest-helper-probe.sh` covers INV-5, INV-6 and INV-7.
- `scripts/wintest-update-e2e.sh` covers INV-8, INV-9 and INV-12.
- Their `.ps1` files live in `scripts/wintest/`.
- The probe re-runs on any change to `_windows_helper_command()`.
- The e2e test runs before the first release carrying this feature, and before any release
  that changes the Windows apply path, the helper, its environment or `PLATFORM_ASSETS`.

## 8. Alternatives considered (and rejected)

- **`Move-Item -Force`, as in FIBR-0131.** It deletes the destination before moving; measured
  above. A move that then fails leaves no `.exe`.
- **A `.ps1` helper file.** It is subject to the execution policy, leaves a file to clean up,
  and could be edited by another process during the wait.
- **Waiting on a PID.** A one-file build is two processes, and Windows reuses PIDs
  (FIBR-0131 D3).
- **Letting the new `.exe` install itself (`--apply-update`).** Its own image is locked while
  it runs. It also adds a command-line route that moves files.
- **`MoveFileEx` through `Add-Type`.** It compiles C# on every update, which is slow and an
  extra thing for antivirus to flag. Two plain renames give the same guarantee.
- **Signed pre-releases on `milnet01/rolodex` with the real key.** `/releases/latest` skips
  pre-releases, so a shipped build would need a feed override to see them, which is the RCE
  ruled out above. Alternatively they would have to be full releases offered to every opted-in
  user. It would also put real-key signatures on throwaway builds.
- **Any runtime override of key or feed** (environment variable, flag, config key).
  Remote code execution for whoever sets it.
- **A local HTTPS feed on the LAN.** The test build would need a different host allowlist and
  CA trust, which is the code under test.
- **Building test `.exe`s on the test PC.** It has no MSYS2 GTK stack; the CI runner is the
  release builder.

## 9. Out of scope

- Authenticode signing and SmartScreen reputation. Deferred; not yet queued.
- A native ARM64 Windows build. Deferred; not yet queued.
- Asking for elevation to update an `.exe` in a protected folder. It fails safely instead
  (§ 6).
- Telling the user in the app that a post-hand-off swap failed. The helper log only.
  Deferred; not yet queued.
- Re-checking the staged digest before the Linux and macOS `os.replace`, which also runs after
  an idle-callback gap. Deferred; not yet queued.
- An MSI, MSIX or other Windows installer.

## 10. What checks this

| Rule | What catches a breach |
|------|----------------------|
| INV-1 | `tests/test_update_windows.py::test_win32_asset_row` |
| INV-2 | `tests/test_update_windows.py::test_selftest_fails_without_asset`; `build.yml` Windows self-test |
| INV-3 | `tests/test_update_windows.py::test_staged_digest_and_tamper` |
| INV-4 | `tests/test_update_windows.py::test_apply_windows_order_and_flags` |
| INV-5 | Partial: `test_helper_script_shape` checks the script; behaviour only by `scripts/wintest-helper-probe.sh`, which no gate runs |
| INV-6 | Partial: as INV-5, leg H3 |
| INV-7 | Partial: as INV-5, leg H4 |
| INV-8 | Partial: `tests/test_update_windows.py::test_helper_env`; inheritance only by the e2e test |
| INV-9 | Partial: `test_helper_script_shape`; the vault check only by the e2e test |
| INV-10 | `tests/test_update_windows.py::test_no_key_or_feed_override` |
| INV-11 | `tests/test_update_test_build.py` |
| INV-12 | **nothing** automatic: an agent runs `scripts/wintest-update-e2e.sh` before release; no hook or CI job requires it |
| § 7 release cadence for the Windows tests | **nothing**: a convention in `CLAUDE.md` |
| S4 | **nothing**: pending the maintainer (Q1) |

The live test proves test builds. A real release differs in four literals and in being
signed by CI with the release key. That CI signing step is shared with the Linux and macOS
assets. The first real Windows update, from the first release carrying this feature to the
next, is the remaining proof.

## 11. Cross-doc impact

| Document | Change owed |
|----------|-------------|
| `docs/specs/ROLO-0037-auto-update.md` | S4, D2's reserved row, § 6's "Running on Windows" row, § 8's "Doing Windows" and § 9's Windows bullet point here. Annotate INV-2, INV-10 and INV-14 "amended by ROLO-0042" for the Windows branch; D9's `/bin/sh` is Linux and macOS only |
| `rolodex.py` comments | The `PLATFORM_ASSETS` comment that calls `win32` "deliberately ABSENT", and `detect_installer`'s docstring, which says a platform without an asset "includes Windows" |
| `SECURITY.md` § The update check | Add the Windows helper, the re-hash, and what it does not defend (same-user write access to the `.exe` folder). Its "Its current state" paragraph still describes the placeholder key, which ROLO-0041 replaced; fix in passing |
| `README.md` | Windows updates in-app; users of 2.0.0 or older install the next release by hand once. The data folder is stated as `%APPDATA%\Rolodex`, but GLib documents the local application-data folder; step 3 of § 4.7 records which, and the README follows it |
| `CLAUDE.md` | Replace "Windows is deliberately absent (deferred)" with the `(win32, AMD64)` row and the never-`Move-Item -Force` rule. Add the Windows-test cadence of § 7 and the two `wintest` scripts |
| `.github/workflows/update-test-build.yml` | New (§ 4.6) |
| `docs/specs/README.md` | Add this spec |
| `CHANGELOG.md`, `ROADMAP.md` | Entry for the release; ROLO-0042 status via `roadmap_log` |

## 12. Cold-eyes loop log

Rows live in `../reviews/ROLO-0042-windows-auto-update-loop-log.md`.

## 13. Resource cost

The helper is one short-lived `powershell.exe`. Its log is truncated on every update and swept
after a day. An e2e run uses three Windows CI builds (public repository) and two short-lived
releases on the test feed. Nothing new is added at runtime.

## 14. Open questions

- **Q1** — The e2e test publishes throwaway test builds as public releases on a new
  repository, `milnet01/rolodex-update-test`, and deletes them after each run. Recommendation:
  allow it. It is the only route found that tests the real GitHub download path without
  adding an override to shipped builds.
