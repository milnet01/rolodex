# Loop log: entries-and-fields.md

Genre: record

Review history for `docs/specs/entries-and-fields.md`, kept outside the spec. One row per
`review-contract` loop, oldest first.

## Cold-eyes loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Q4 | Outcome |
|------|------|-------|----|----|----|----|---------|
| 1 | 2026-10-19 | 2, cold, headless (`neutral-lane`), every lane holding every question — genre pinned `spec`; trigger ROLO-0097 (commit 729dd81, INV-9 gains `seed` and `mnemonic`) | 1 | 1 | 2 | 0 | **Four verified: three fixed, one surfaced to the user; none dismissed.** **Both lanes found the central defect, on the text that armed the gate [Q3]:** INV-9 does not say whether `seed`/`mnemonic` reach fields already stored with `sensitive: false`. `migrate_vault` only `setdefault`s the flag and `field_is_sensitive` reads the stored flag or a TOTP match, so changing the constant alone leaves an existing "Seed phrase" in cleartext; re-deriving at render instead collides with INV-10's user override. A design decision, surfaced on ROLO-0097, not invented here. **Both lanes found [Q2]:** INV-10's "either way" is false for a field `parse_totp_field` recognises, which the editor's save and `field_is_sensitive` always mask (`totp-codes.md` INV-15/16); INV-10 now states the exception. **One lane found [Q3]:** INV-9 omitted the `FieldRow._sens_user_set` latch; added. **[Q1], from a lane's open question settled by the orchestrator:** INV-18's markup-sink list omitted the toast, which `MainWindow._toast` escapes because `Adw.Toast` defaults `use-markup` to True (checked: `Adw.Toast(title='x').get_use_markup()` printed `True`); added. The last three are pre-existing and outside ROLO-0097's radius, fixed in-run as exempt records (each records code that already exists). One open question resolved clean: `TOTP_LABEL_KEYWORDS` holds neither new keyword, so the "overlap only on authenticator" statements stay true. Lane line counts matched disk (98). |
