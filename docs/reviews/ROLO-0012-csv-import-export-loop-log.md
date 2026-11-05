# Loop log: ROLO-0012-csv-import-export.md

Genre: record

Review history for `docs/specs/ROLO-0012-csv-import-export.md`, kept outside the spec. One row
per `review-contract` loop, oldest first.

## Cold-eyes loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Q4 | Outcome |
|------|------|-------|----|----|----|----|---------|
| 1 | 2026-11-05 | 2, headless, every lane held every question | 0 | 4 | 1 | 1 | 6 verified, 6 fixed (5 found by both lanes or one lane and the packet build), 1 dismissed: a missing category-heading path in INV-11's escaping list changes nothing built, since category names render through plain-text labels; the false word "only" was deleted. Fixed: kept timestamps on import (INV-1 now runs through `import_entries`), empty-file and entry-cap checks moved before detection, `rolodex` rows' sensitivity from the hidden column only, the `csv` field-size limit raised, the GUI-side tests for INV-11 and INV-12 moved to `test_widgets.py`. Also fixed: three bare cross-document INV ids qualified. Open questions resolved clean: `parse_totp_field("TOTP", "not-a-seed")` returns None (run). Foreign headers other than Chromium's were not re-fetched. |
| 2 | 2026-11-05 | 2, headless, every lane held every question | 0 | 1 | 1 | 1 | 3 verified, 2 fixed, 1 surfaced to the user, 1 dismissed. Fixed: a field's value is never stripped (Q3), and INV-1's fixture gains a `\r\n` note, since a `\n`-only note round-trips with or without `newline=""` (Q4; measured with `python3 -I`). Surfaced, not fixed: a vault over `MAX_IMPORT_ENTRIES` or `MAX_IMPORT_BYTES` exports but cannot re-import whole, against § 1's lossless promise (Q2); the remedy is a design choice. Dismissed: INV-11's category-name route, again; every category display found renders plain text (no `set_markup` or `use_markup` in `rolodex.py`, every `Adw.ActionRow` title with file text escaped). A count ("two of the five writers") that disagreed with INV-2 was deleted. Open questions resolved clean: Bitwarden's individual export sets `folder` and `favorite` (fetched). Cap reached (2 of 2), calm: 1 of 3 verified findings anchored on text this run wrote (the § 4.6 order, as an unpropagated consequence of the caps, not a repair of a repair). This loop's 2 fixes were read by no lane. Status set to accepted. |
