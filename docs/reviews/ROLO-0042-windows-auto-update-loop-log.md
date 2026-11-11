# Loop log: ROLO-0042-windows-auto-update.md

Genre: record

Review history for `docs/specs/ROLO-0042-windows-auto-update.md`, kept outside the spec. One row
per `review-contract` loop, oldest first.

## Cold-eyes loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Q4 | Outcome |
|------|------|-------|----|----|----|----|---------|
| 1 | 2026-11-11 | 2, headless, every lane held every question | 1 | 0 | 1 | 4 | 6 verified, 6 fixed, 1 dismissed. Four were found by both lanes. Q4: INV-7's "`Get-FileHash` precedes the first `Move-Item`" fails on the § 4.3 script, whose `Try5` definition holds the first `Move-Item`; INV-6 and INV-7 now order `Try5` calls. Q4: INV-9's test extracted every single-quoted literal, and the script's messages and digest are literals too; it now checks the three assignments and that no other literal holds `\`. § 4.5 no longer says the helper names its log, which the app opens. Q3: "occur exactly once" now names the assignment lines; the literal `"rolodex"` occurs five times. Q1: the test workflow copied only `build.yml`'s MSYS2 step, while `pip install --upgrade pyinstaller certifi` sits in the build step, so a test build could lack certifi. Q4 (one lane): `test_selftest_fails_without_asset` passed regardless, because a faked frozen build fails the typelib check first; it now supplies a fake `_MEIPASS`. Q4 (one lane): INV-10 did not cover a feed override placed in `check_for_update`. Dismissed, not material: INV-1's "or suffix" (suffix matching cannot match `.sig` or `.zip`; ROLO-0037 INV-5 owns matching); the false clause was deleted. Also deleted, not tallied: § 4.5's "a day after they were last written", It was a lane's open question, not settled here. Four open questions resolved clean on `ssh wintest` (PowerShell 5.1.19041): `'ABC' -ne 'abc'` is `False`; `whoami` over ssh is `sparepc\ant` with profile `C:\Users\aants`, the desktop user; `Move-Item -Destination` and `Start-Process -FilePath` work in folders named `a [b] c`, `x[1]y` and `it's`. Unrunnable region declared to the lanes: the frozen build, the helper's runtime and the two-cycle test. The packet build found no defect. Lanes disclosed the global `CLAUDE.md`, a hook notice and an email context; no git snapshot. |
