# Spec: Import, Export, Backup & Restore

Retroactive spec for the data-movement features (`parse_text_file`, `import_entries`,
`ImportPreviewDialog`, and the backup/restore/export handlers on `MainWindow`).

## Text import

- **INV-1** The importer splits the file into blocks separated by blank lines. Each block is
  one entry: the first line is the `name`, with `rstrip(":")` then `strip()` applied — so all
  trailing colons are removed, but a colon followed by whitespace (e.g. `"Name: "`) is not.
- **INV-2** Within a block, a line matching the regex `^([^:]+?):\s+(.+)$` becomes a field
  `{label, value, sensitive}`. Consequences: the label may **not** contain a colon (the first
  colon splits label from value), and there must be at least one whitespace character (space or
  tab) after the colon. The captured value is then `strip()`ped, so a colon followed by only
  whitespace yields an empty value. `sensitive` is auto-detected from the label via the same
  `SENSITIVE_KEYWORDS` rule used elsewhere.
- **INV-3** Non-matching non-empty lines in a block are collected into the entry's `notes`
  (joined by newlines).
- **INV-4** The import file picker opens in the user's home directory (no hardcoded path).
- **INV-5** Both parse failure and an empty parse surface via the same `_show_message` dialog —
  parse failure with title "Import Error" (the exception text), an empty parse with title
  "Import" and body "No entries found in file." Neither modifies the vault.

## Import preview & commit

- **INV-6** `ImportPreviewDialog` lists every parsed entry with its field count, a `+notes`
  marker, and a `(duplicate)` marker when an entry with the same name (case-insensitive)
  already exists. Duplicates are unchecked by default; non-duplicates checked.
- **INV-7** "Select All" / "Select None" toggle every checkbox; only checked entries import.
- **INV-8** `import_entries` skips entries whose name (case-insensitive) already exists when
  `skip_duplicates` is true, counting imported vs skipped. Entries are added via `add_entry`
  (fresh UUID + timestamps) inside `import_entries`; `import_entries` itself does not persist —
  the caller `_finish_import` saves the vault afterwards.

## Encrypted backup

- **INV-9** Backup first saves the current vault, then copies the encrypted `contacts.vault`
  byte-for-byte to the chosen path and `chmod`s it to `0600`. The backup is encrypted with the
  *current* master password (it is a copy of the live file).
- **INV-10** The default backup filename is `contacts_backup_<YYYYMMDD>_<HHMMSS>.vault`.

## Restore

- **INV-11** Restore requires confirmation (it replaces all current entries). The user then
  selects a backup file and enters **that backup's** master password.
- **INV-12** Decryption runs off the UI thread; a wrong password shows "Wrong password for this
  backup." and does not alter the current vault.
- **INV-13** On success the restored vault is migrated, becomes the live vault with its own salt
  and password, and is immediately re-saved to `contacts.vault` (so the app's file now uses the
  backup's password). The detail pane clears and a count toast is shown.

## Plaintext export

- **INV-14** Export requires confirmation because it writes **unencrypted** data.
- **INV-15** The export is a human-readable text dump (name, optional category, aligned
  label/value pairs, optional notes) written with `os.open(..., 0o600)`, so the `0600` mode
  applies when the file is created. Overwriting an existing file keeps that file's current
  permissions — the export path does not additionally `chmod` (unlike the backup path).
- **INV-16** The default export filename is `rolodex_export_<YYYYMMDD>_<HHMMSS>.txt`.

## Notes

- Backup/restore round-trips ciphertext; export is a deliberate one-way plaintext escape hatch.
- Interaction gotcha: the preview lets you tick a name marked "(duplicate)", but `_finish_import`
  calls `import_entries` with `skip_duplicates=True`, so a manually re-checked duplicate is still
  skipped. This surprises users — a candidate fix, not yet scheduled.
- CSV import/export is roadmap ROLO-0012.
