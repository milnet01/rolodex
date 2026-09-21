# Review record — `docs/documentation-standards.md`

Genre: record
Status: active

Cold-review loop log for the documentation standard, kept here rather than in the document
itself, as the project does for its other standards. The document carries a one-line pointer.

Gate: `review-contract docs/documentation-standards.md --genre standard` (cap 3).
Trigger: ROLO-0040 — two Style rules added on 2026-08-27 (no counts or line numbers; every claim
checkable) changed what a conformer writes, and the gate owed on them had not been run.

## Loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Outcome |
|------|------|-------|----|----|----|---------|
| 1 | 2026-09-18 | 3, cold — genre pinned `standard` | 2 | 3 | 0 | **Five verified, five fixed; one dismissed.** **All three lanes found the same Q1:** the table told a conformer to update `ROADMAP.md` in the same commit, and that file is now generated from the Ants roadmap store — a hand edit is discarded on the next store write. The row and the document-set entry now route roadmap changes through `roadmap_log`. **All three lanes found the banner example false:** `# ---- Encryption layer ----` matches no banner in `rolodex.py`, which uses three-line blocks, and the GUI boundary uses `=` rules and is found by a test (`test_ROLO0052`) through its exact name line; a conformer copying the example would have broken that test. **Two lanes found the behaviour row omitting the spec invariants** that `docs/specs/README.md` requires to move in the same commit. **Two lanes found the vault-schema row omitting `docs/file-naming.md`'s key list** (a declared serialization contract), and one also `entries-and-fields.md`; the crypto row likewise omitted `coding-standards.md`, which carries the `0600` fact. All added. **One lane found "retroactive contracts" contradicting the specs index**, which also holds forward `ROLO-NNNN` specs; the entry now defers to the index. **Dismissed as immaterial:** "docs/*.md — the standards" beside review records in `docs/`; no conformer writes anything differently. **Collateral outside the subject, corrected in place:** `coding-standards.md` prescribed `os.open(..., O_TRUNC, 0o600)` for vault writes, the form `security-standards.md` non-negotiable 2 forbids because it loses atomicity; it now names `write_private_file()`. **The Style rules that armed the gate drew no finding.** One packet defect of the orchestrator's: `rolodex.py` was edited mid-loop (ROLO-0026), so one lane's line numbers disagreed with the packet grep by three lines. |
| 2 | 2026-09-18 | 3, cold — identical brief, packet rebuilt from disk | 3 | 0 | 0 | **Three verified, three fixed; nothing dismissed.** **All three lanes found the schema/format row exempting `DESIGN.md`** on the ground that it carries no literal field list — true of the field list, false of the byte layout, which `DESIGN.md` § On-disk format states literally (`VLT1` + salt + Fernet token). A conformer changing the magic or salt length would have left it stale; the row now requires that section for a byte-layout change. **One lane found the row's "README.md (format one-liner)"** naming a line that does not exist — the README has only a link to the vault spec, now what the row names. **One lane found loop 1's own banner fix incomplete:** it named the GUI boundary as the one `=` banner, and `# Application` is one too. Two more stray `=` lines sat on top of ordinary `-` banners in `rolodex.py`; those were deleted rather than documented, so the rule now describes the code exactly. **Collateral outside the subject, corrected in place:** `coding-standards.md` still described `_save()` as re-deriving the key synchronously on every save, the gap ROLO-0043 closed; it now states the rule the project `CLAUDE.md` gives. One of the three findings landed on text this run wrote. |
| 3 | 2026-09-18 | 3, cold — identical brief, packet rebuilt from disk | 2 | 0 | 0 | **Two verified, two fixed; nothing dismissed. Cap reached (3 for a standard).** **Two lanes found the crypto/permissions row still incomplete** — `import-export-backup.md` and `master-password.md` also state the `0600` and KDF facts. This is the third loop to find a gap in the table's file lists, so the fix is not another name: the document now says the lists name the main homes, and prescribes a search for the fact itself before committing (`grep -rlE '0600|600,000|600k|ITERATIONS' --include=*.md .`, run and confirmed to return every home, both missed specs included). **Two lanes found "docs/*.md — the standards"** beside review records, one of which this document's own first line points at; loop 1 dismissed the same point as immaterial, and this time two lanes named the harm — a conformer applying the no-counts Style rule to a loop log made of counts. Now stated. Open questions not filed: `categories.md` for a schema change (covered by the new search rule); banners of more than three lines (the auto-update banner carries note lines; nothing a conformer writes differs). |

## Exit — cap reached at loop 3

**Calm or violent:** one of loop 3's two findings landed on a row this run had already edited
(the permissions row, extended in loop 1); the other was pre-existing. A mixed share, and the
repeated finding was structural — a closed list that could never be complete — rather than the
run repairing its own prose. Read as a **calm cap**: the list problem is now answered by a search
rule instead of by enumeration.

**Share of the run on the change that armed the gate** (the two 2026-08-27 Style rules): none of
the ten verified findings anchored there. The run audited the rest of the document.

**Deferred tail: empty.** Every verified finding was fixed. Neighbour corrections made in passing:
`coding-standards.md` (the `O_TRUNC` write form, loop 1; save-time key derivation, loop 2) and
two stray `=` rules in `rolodex.py` (loop 2).
