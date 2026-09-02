# Review record — `docs/security-standards.md`

Cold-review loop log for the security standard, kept here rather than in the
document itself: the standard has never carried one, and no project standard
requires one of a standard. The document carries a one-line pointer to this file.

Gate: `review-contract docs/security-standards.md --genre standard` (cap 3).
Trigger: ROLO-0062 — the 2026-08-31 audit rewrote non-negotiable 2 and the
change of direction owed a cold read that had not been run.

## Loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Outcome |
|------|------|-------|----|----|----|---------|
| 1 | 2026-09-02 | 3, cold — genre pinned `standard` | 0 | 1 | 3 | **Four verified, four fixed; nothing dismissed. Not one Q1** — every claim the document makes about the code checked out, including four the lanes could not settle from the packet and the orchestrator resolved (`.rolodex.conf` is written atomically; `parse_text_file` has no `eval`/`exec`; the plaintext export really is behind a confirmation dialog; the salt really is `os.urandom(16)`). **All three lanes independently found the same Q2**, the strongest signal in the run: non-negotiable 2 forbids `shutil.copy2` + `chmod` while citing `vault-format-and-crypto.md` INV-9 as its authority, and INV-9 said "Backups `shutil.copy2` then `os.chmod(..., 0o600)`." The code sides with the standard — `_on_backup_file_chosen` routes through `write_private_file` — so INV-9 was the stale half, and a conformer chasing the citation would have rebuilt the 0644 window and the truncate-the-previous-backup failure the rule exists to forbid. **Fixed at the source in INV-9 rather than by qualifying the citation**, which left the subject needing no edit for this finding. **Two lanes found the Q3 with the sharpest consequence:** rule 3 said a signing key is "covered too" without saying by which clause, and the two readings differ destructively — rule 2's `write_private_file` ends in `os.replace`, which overwrites silently, where `O_EXCL` refuses. Verified by execution, not inference: `write_private_file` over an existing file returned `REPLACED`, `O_EXCL` raised `FileExistsError`. A conformer "complying" with rule 2 on a key path would destroy a release private key and invalidate every published signature. **Two lanes found the checklist testing the wrong thing** — "Any newly written vault/secret file is `0600`" tests the end state, which rule 2 says outright is insufficient, so a reviewer ticks the box on the exact `copy2`+`chmod` code rule 2 forbids. **One lane found the subprocess bullet scoped to "(clipboard)"** while `shell=True` should bind everywhere, and a non-clipboard `subprocess.Popen` already exists at the update relaunch. **The 4b sweep found three more stale copies no lane could see**, none of them in the packet: `import-export-backup.md` INV-9 and INV-15's parenthetical both still described the backup as `chmod`-after-copy, and `SECURITY.md`'s permissions table — the outward-facing policy this standard's own opening says must agree with it — said "backups are `chmod 0600` right after the copy". All three corrected. One mechanical fix outside the tally: the Non-negotiables list numbered its items 1, 2, 3, 4, 4, 5; renumbered after confirming nothing cites these rules by number. |
| 2 | 2026-09-02 | 3, cold — identical brief, packet rebuilt from disk | 0 | 4 | 1 | **Five verified, five fixed; nothing dismissed. Again not one Q1.** **Two lanes independently found the sharpest contradiction:** rule 1 forbids "temp files ... that contain field values", while rule 2 mandates routing every secret write through `write_private_file()`, which stages exactly such a temp for the *plaintext* export. As written the two rules forbid and require the same mechanism, and an implementer adding a second plaintext path would obey rule 1, skip the staging temp and write the destination directly — losing atomicity and the overwrite mode. **One lane found that rule 2's headline promised only creation**: "is created `0600`", where INV-9's guarantee is emphatic that an overwrite ends `0600` too — and that the `O_TRUNC` note blamed only INV-16 when that form also broke the permission guarantee. Verified by execution: re-opening a `0644` file with `O_TRUNC, 0o600` left it `0644`, because the mode argument applies only on creation. Both fixed. **One lane found two conflicts with `dependency-management-standards.md`** — this document said "keep `cryptography` reasonably current" and "prefer the latest" where that standard states latest stable as an obligation with a formal forced-older-pin process, so a conformer holding an old release was compliant here and in breach there; and nothing settled whether the `>=44.0.0` CVE floor overrides that pin exception, so two conformers could cite a standard for shipping a CVE-bearing crypto library or for blocking the release. The floor is now stated as overriding. **One lane found the KDF rule naming a mechanism that does not exist:** "may be raised (with a migration path)" — but the header records no iteration count, so raising the constant makes every existing vault fail as `InvalidToken`, which the unlock dialog renders as "Wrong password." A raise is a format change needing a new magic; the rule now says so. **The 4b sweep found `vault-format-and-crypto.md` INV-5 carrying the identical incomplete claim**, corrected there. **All three lanes reported that `write_private_file` appeared to swallow write errors — that was the orchestrator's packet window cut one line short of the `raise`, not a code defect.** Each lane correctly routed it to Open questions rather than filing it, which is the finder-not-verifier rule working. |
| 3 | 2026-09-02 | 3, cold — identical brief, packet rebuilt from disk with the loop-2 window defect fixed | 0 | 2 | 1 | **Three verified, three fixed; one dismissed. Cap reached (3 for a standard) — the run files its tail and exits. A third loop with no Q1 either.** **Two of the three landed on text THIS RUN wrote, which is a high share and reads as the run settling its own prose rather than the document.** One lane found loop 2's KDF fix contradicting itself two lines apart: "The header carries no iteration count" followed by "a `migrate_vault` branch that reads the old count from the header" — there is no such field, so an implementer would parse the salt as a count. The mechanism is to INFER 600,000 from the old `VLT1` magic and record the count in the new format. A second lane raised the same pair as an open question and declined to file it because the identical wording sat in `vault-format-and-crypto.md` INV-5 — correctly, and both are fixed. One lane found loop 2's dependency override asserted only in this document, while `dependency-management-standards.md` presents its forced-older-pin exception as closed; a conformer reading only that document would pin below the CVE floor and never learn it existed. Two other lanes explicitly declined that one as not a contradiction, and they were right that it is not — the breach is simply invisible from the document that permits it, so the precedence is now stated there too. **The one pre-existing find is the checklist mismatch again, the same shape loop 1 fixed elsewhere:** "KDF iterations ... unchanged or strengthened, with migration if the on-disk format changed" gates migration on the byte layout, which a raise does not alter — so a reviewer ticks "strengthened", answers "format changed? No", and merges a bump that locks every existing vault out. **Dismissed, recorded:** one lane wanted "adding a crypto dependency requires explicit review" filed for naming no artefact; another lane explicitly declined it and the genre rule agrees — a conformer knows whether they sought review, so it is a principle a standard may state. **Open questions all resolved clean:** the unlock string is exactly "Wrong password." (`rolodex.py:1390`), and the standard's silence on `MIN_PASSWORD_LENGTH` is an absence, which no question asks about. **A second orchestrator packet defect this loop** — the unlock window was cut from stale line numbers, my own edits having shifted them; two lanes routed it to Open questions rather than filing, as designed. |

## Findings not carried into the subject

Corrected in the neighbouring document instead, per the rule that a neighbour's
defect must never become collateral in the subject:

- `docs/specs/vault-format-and-crypto.md` INV-9 — backup write mechanism.
- `docs/specs/import-export-backup.md` INV-9 and INV-15 — same claim, two more copies.
- `SECURITY.md` permissions table — same claim, outward-facing copy.
- `docs/specs/vault-format-and-crypto.md` INV-5 — the KDF-raise claim, loops 2 and 3.
- `docs/dependency-management-standards.md` — the security-floor precedence, loop 3.

## Exit — cap reached at loop 3

**Calm or violent:** two of loop 3's three verified findings landed on text this run
wrote, a high share. By the measure that is a **violent cap** — the run had begun
settling its own prose. The trajectory qualifies it: loop 1 fixed four pre-existing
defects, loop 2 four pre-existing and one partly its own, loop 3 one pre-existing and
two of its own additions. The document's own defects were drying up.

**Consequence, per the rule:** this ends the review of the document as it stands. A
fourth loop is not filed, and this gate should not be re-run on this text. An authoring
edit that changes direction re-arms it normally.

**Size is not the problem.** The subject is 105 lines, small for its genre, so the cap
binding is not the split signal — it is the run's own additions being refined.

**Share of the run that landed on the change that armed the gate:** roughly a quarter of
the twelve verified findings across three loops anchor inside the span ROLO-0062 named
(the 2026-08-31 rewrite of non-negotiable 2 and the signing-key clause). The rest was
audit rather than gate. No consequence attaches to that number here; it is recorded so
the audit can later be triggered deliberately rather than taken as a side effect.

**Deferred tail: empty.** Every verified finding was fixed; the single dismissal is
recorded in loop 3's row above.
