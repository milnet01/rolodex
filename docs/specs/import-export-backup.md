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
- **INV-5a** A file larger than `MAX_IMPORT_BYTES` (10 MB) is refused before it is read in
  full. `parse_text_file` raises `ValueError`, which INV-5's "Import Error" dialog shows
  (ROLO-0050).

## Import preview & commit

- **INV-6** `ImportPreviewDialog` lists every parsed entry with its field count, a `+notes`
  marker, and a duplicate marker. `duplicate_flags` decides the marker: a name matching a vault
  entry **or an earlier entry in the same file**, under `name_key` (case-insensitive,
  whitespace-trimmed). Duplicates are unchecked by default; non-duplicates checked.
- **INV-7** "Select All" / "Select None" toggle every checkbox. Exactly the checked entries
  import, **a checked duplicate included** — it lands as a second entry with the same name
  (ROLO-0047).
- **INV-7a** The preview carries an "Add to category" picker listing "No category" and the
  vault's categories. Every imported entry is filed under the choice; the default is
  uncategorised (ROLO-0067).
- **INV-8** `import_entries(vault, parsed, skip_duplicates=True, category="")` skips entries
  `duplicate_flags` marks when `skip_duplicates` is true, and returns `(imported, skipped)`. The
  preview's commit path, `_finish_import`, passes `skip_duplicates=False`, because the preview
  has already applied the user's choice. An unknown `category` raises `ValueError`. Entries are
  added via `add_entry` (fresh UUID + timestamps) inside `import_entries`; `import_entries`
  itself does not persist — `_finish_import` saves the vault afterwards.

## Encrypted backup

- **INV-9** Backup first saves the current vault, then writes the encrypted `contacts.vault`
  byte-for-byte to the chosen path through `write_private_file` — `0600` from creation, and
  atomic, so an interrupted backup cannot destroy a previous one. The backup is encrypted with
  the *current* master password (it is a copy of the live file).
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
  label/value pairs, optional notes) written through `write_private_file`, which stages a
  `0600` temp and `os.replace`s it into place — so the export is `0600` on creation **and** on
  overwrite, without needing its own `chmod`. Before
  1.3.1 it used `os.open(..., 0o600)` directly and an overwrite kept the existing file's
  permissions; see `vault-format-and-crypto.md` INV-9.
- **INV-15a** The staged temp lives in the directory the user picked, not a private one. That is
  accepted: the export itself lands in that directory, and a same-directory temp is what makes
  the write atomic. A SIGKILL or power cut can leave that temp behind as a `.rolodex-*.tmp`
  dotfile holding the plaintext (ROLO-0064).
- **INV-16** The default export filename is `rolodex_export_<YYYYMMDD>_<HHMMSS>.txt`.

## Notes

- Backup/restore round-trips ciphertext; export is a deliberate one-way plaintext escape hatch.
- CSV import/export is roadmap ROLO-0012.
