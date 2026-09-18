# Review record — `docs/documentation-standards.md`

Cold-review loop log for the documentation standard, kept here rather than in the document
itself, as the project does for its other standards. The document carries a one-line pointer.

Gate: `review-contract docs/documentation-standards.md --genre standard` (cap 3).
Trigger: ROLO-0040 — two Style rules added on 2026-08-27 (no counts or line numbers; every claim
checkable) changed what a conformer writes, and the gate owed on them had not been run.

## Loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Outcome |
|------|------|-------|----|----|----|---------|
| 1 | 2026-09-18 | 3, cold — genre pinned `standard` | 2 | 3 | 0 | **Five verified, five fixed; one dismissed.** **All three lanes found the same Q1:** the table told a conformer to update `ROADMAP.md` in the same commit, and that file is now generated from the Ants roadmap store — a hand edit is discarded on the next store write. The row and the document-set entry now route roadmap changes through `roadmap_log`. **All three lanes found the banner example false:** `# ---- Encryption layer ----` matches no banner in `rolodex.py`, which uses three-line blocks, and the GUI boundary uses `=` rules and is found by a test (`test_ROLO0052`) through its exact name line; a conformer copying the example would have broken that test. **Two lanes found the behaviour row omitting the spec invariants** that `docs/specs/README.md` requires to move in the same commit. **Two lanes found the vault-schema row omitting `docs/file-naming.md`'s key list** (a declared serialization contract), and one also `entries-and-fields.md`; the crypto row likewise omitted `coding-standards.md`, which carries the `0600` fact. All added. **One lane found "retroactive contracts" contradicting the specs index**, which also holds forward `ROLO-NNNN` specs; the entry now defers to the index. **Dismissed as immaterial:** "docs/*.md — the standards" beside review records in `docs/`; no conformer writes anything differently. **Collateral outside the subject, corrected in place:** `coding-standards.md` prescribed `os.open(..., O_TRUNC, 0o600)` for vault writes, the form `security-standards.md` non-negotiable 2 forbids because it loses atomicity; it now names `write_private_file()`. **The Style rules that armed the gate drew no finding.** One packet defect of the orchestrator's: `rolodex.py` was edited mid-loop (ROLO-0026), so one lane's line numbers disagreed with the packet grep by three lines. |
