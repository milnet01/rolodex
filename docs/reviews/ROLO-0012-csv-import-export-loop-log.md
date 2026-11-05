# Loop log: ROLO-0012-csv-import-export.md

Genre: record

Review history for `docs/specs/ROLO-0012-csv-import-export.md`, kept outside the spec. One row
per `review-contract` loop, oldest first.

## Cold-eyes loop log

| Loop | Date | Lanes | Q1 | Q2 | Q3 | Q4 | Outcome |
|------|------|-------|----|----|----|----|---------|
| 1 | 2026-11-05 | 2, headless, every lane held every question | 0 | 4 | 1 | 1 | 6 verified, 6 fixed (5 found by both lanes or one lane and the packet build), 1 dismissed: a missing category-heading path in INV-11's escaping list changes nothing built, since category names render through plain-text labels; the false word "only" was deleted. Fixed: kept timestamps on import (INV-1 now runs through `import_entries`), empty-file and entry-cap checks moved before detection, `rolodex` rows' sensitivity from the hidden column only, the `csv` field-size limit raised, the GUI-side tests for INV-11 and INV-12 moved to `test_widgets.py`. Also fixed: three bare cross-document INV ids qualified. Open questions resolved clean: `parse_totp_field("TOTP", "not-a-seed")` returns None (run). Foreign headers other than Chromium's were not re-fetched. |
