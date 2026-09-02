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

## Findings not carried into the subject

Corrected in the neighbouring document instead, per the rule that a neighbour's
defect must never become collateral in the subject:

- `docs/specs/vault-format-and-crypto.md` INV-9 — backup write mechanism.
- `docs/specs/import-export-backup.md` INV-9 and INV-15 — same claim, two more copies.
- `SECURITY.md` permissions table — same claim, outward-facing copy.
