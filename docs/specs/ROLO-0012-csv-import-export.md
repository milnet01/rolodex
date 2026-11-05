<!-- ants-spec-format: 1 -->
# ROLO-0012 — Import and export CSV

Genre: spec
Status: draft
Describes: planned

**Status:** spec draft (2026-11-05).
**Kind:** implement.
**Source:** ROADMAP ROLO-0012 (in-session 2026-07-04; scope decision by the user 2026-09-25).

**Layman:** Move your passwords in from Bitwarden, KeePassXC, Chrome, Firefox or 1Password,
and out to a spreadsheet file, without losing anything on the way back in.

## 1. Goal

Rolodex can read the CSV export of five other password managers, and any other CSV after the
user says which column is which. It can write its own vault as a CSV that Rolodex reads back
into exactly the entries it came from.

## 2. Problem

The only importer is `parse_text_file`, which reads one bespoke text layout
(`import-export-backup.md` INV-1 to INV-3). The only export, `MainWindow._on_export_file_chosen`,
writes a human-readable dump that nothing parses back. So:

1. A user leaving another manager must retype every credential.
2. A Rolodex user has no machine-readable way out, and no plaintext copy that restores.

## 3. Scope decisions (agreed with the user)

- **The five formats** are Bitwarden, KeePassXC, Chrome/Edge/Brave, Firefox and 1Password. An
  unrecognised header falls back to a column-mapping step. Export writes Rolodex's own layout.
  The user, 2026-09-25 (ROADMAP ROLO-0012).
- **The export is lossless: one row per entry, each field a group of columns.** Re-importing
  it gives back what was exported. The user, 2026-11-05, choosing it over a
  common-columns layout and a one-row-per-field layout.
- **Deduced from that choice, not separately agreed** (the author, 2026-11-05): each field
  carries a third column for its Hide flag, and each entry carries `created` and `modified`.
  Without them a field the user hid by hand, under a label no keyword matches
  (`entries-and-fields.md` INV-10), comes back visible, and every timestamp is reset.
- **The author's call, 2026-11-05, for the user to overturn:** the export does not neutralise
  spreadsheet formulas (§ 8, *Formula escaping*).

## 4. Design

All parsing and writing lives in the pure layer, beside `parse_text_file`, using the standard
library's `csv` module. No new dependency.

### 4.1 Reading the file

```python
def read_csv_file(filepath: str) -> tuple[list[str], list[list[str]]]:
    """(header, rows). Raises ValueError on a malformed or oversized file."""
```

- Opened with `encoding="utf-8-sig"` (strips a UTF-8 byte-order mark, which a spreadsheet
  re-save adds) and `newline=""` (so a quoted multi-line cell stays one cell).
- The `MAX_IMPORT_BYTES` check runs exactly as in `parse_text_file`, raising `ValueError`
  with `_IMPORT_TOO_LARGE`.
- Comma delimiter only. `csv.Error` (including a cell over the module's field-size limit) is
  re-raised as `ValueError` naming the row number.
- The first row is the header. A row whose cells are all empty is dropped. A shorter row is
  padded with empty cells. **A row longer than the header raises `ValueError`**, because the
  extra cells have no column to go to and dropping them would lose data silently.
- An empty file, or a header with no rows, returns no rows; the caller shows INV-5's
  "No entries found in file."

### 4.2 Recognising the format

```python
CSV_FORMATS: dict[str, frozenset[str]]  # format id -> required columns
def detect_csv_format(header: list[str]) -> str | None:
```

Header cells are compared after `strip()` and `casefold()`. The writers differ in case and
quoting (Source: each writer, cited in § 4.3).

- **`rolodex`** is recognised by structure: the header is exactly `name, category, notes,
  created, modified`, then zero or more groups `field_N_label, field_N_value, field_N_hidden`
  with N counting up from 1.
- **Every other format** is recognised when the header contains all of its required columns.
  Extra columns are allowed, because two of the five writers have added columns in recent
  versions (§ 4.3).
- **Exactly one format must match.** No match, or more than one, goes to the mapping step
  (§ 4.5). There is no precedence order between formats.

| Id | Required columns |
|---|---|
| `bitwarden` | `type`, `name`, `notes`, `fields`, `login_uri`, `login_username`, `login_password`, `login_totp` |
| `keepassxc` | `group`, `title`, `username`, `password`, `url`, `notes` |
| `chromium` | `name`, `url`, `username`, `password` |
| `firefox` | `url`, `username`, `password`, `httprealm`, `formactionorigin`, `guid` |
| `1password` | `title`, `url`, `username`, `password`, `otpauth`, `notes` |

### 4.3 Turning rows into entries

Each row becomes one parsed entry, the dict `parse_text_file` already returns, plus optional
keys:

```python
{"name": str, "fields": [{"label", "value", "sensitive"}], "notes": str,
 "category": str,                       # optional; "" or absent = none
 "created": str, "modified": str}       # optional; rolodex format only
```

Columns map to a target: **Name**, **Category**, **Notes**, a **field** with a fixed label,
or **Drop**. A column the table does not name becomes a field labelled with its header text,
as typed in the file. An empty cell never becomes a field.

| Format | Name | Category | Notes | Fields (label ← column) | Dropped |
|---|---|---|---|---|---|
| `bitwarden` | `name` | `folder`, else `collections` | `notes` | Username ← `login_username`, Password ← `login_password`, URL ← `login_uri`, TOTP ← `login_totp`, plus each line of `fields` | `type`, `favorite`, `reprompt`, `archivedDate` |
| `keepassxc` | `Title` | `Group`, first path segment removed | `Notes` | Username, Password, URL, TOTP ← the like-named column | `Icon`, `Last Modified`, `Created` |
| `chromium` | `name` | — | `note` | Username, Password, URL ← `username`, `password`, `url` | — |
| `firefox` | host of `url` | — | — | Username, Password, URL | `httpRealm`, `formActionOrigin`, `guid`, `timeCreated`, `timeLastUsed`, `timePasswordChanged` |
| `1password` | `Title` | — | `Notes` | Username, Password, URL ← `Url`, TOTP ← `OTPAuth`, Tags ← `Tags` | `Favorite`, `Archived` |

Sources, read 2026-11-05:
- Bitwarden: <https://github.com/bitwarden/clients/blob/main/libs/tools/export-vault-core/src/services/base-vault-export.service.ts>
- KeePassXC: <https://github.com/keepassxreboot/keepassxc/blob/develop/src/format/CsvExporter.cpp>
- Chromium: <https://github.com/chromium/chromium/blob/main/components/password_manager/core/browser/export/password_csv_writer.cc>
- Firefox: <https://github.com/mozilla-firefox/firefox/blob/main/toolkit/components/passwordmgr/LoginExport.sys.mjs>
- 1Password does not publish its header. The column names come from two independent importers
  that agree (Keyguard's `OnePasswordCsvImporter.kt`, 2FAS's `OnePasswordImporter.kt`), and
  their order matches <https://support.1password.com/export/>.

Rules the table needs:

- **Bitwarden `fields`** holds custom fields, one `name: value` per line. Each line splits at
  its first `": "` into a field. A line without one is appended to notes.
- **KeePassXC `Group`** is a `/`-joined path starting with the root group's name, which is
  translated and not always `Root`. The first segment is removed whatever it says; what
  remains, still `/`-joined, is the category. Nothing left means no category.
- **Name fallback.** An empty name takes the URL field's value, else `"Untitled"`. This holds
  for every format, including Firefox's host rule when `url` does not parse to a host.
- **Sensitivity.** A field is sensitive when `is_sensitive_label(label)` or
  `parse_totp_field(label, value)` says so, the rule `parse_text_file` uses. **A TOTP field is
  always sensitive**, even when its value fails `parse_totp_field`, so a malformed seed is
  never shown in clear.
- **`rolodex` rows** map structurally. `name`, `category` and `notes` map to themselves. Each
  field group with a non-empty label or value becomes a field in N order.
  `sensitive = hidden.strip().casefold() != "no"`, so an unreadable cell hides the field
  rather than exposing it. `created` and `modified` are kept when
  `datetime.fromisoformat` parses them, else dropped.

```python
def csv_rows_to_entries(fmt: str, header: list[str], rows: list[list[str]]) -> list[dict]:
```

### 4.4 Categories on import

`import_entries` gains one meaning for its existing parameter: **`category=None` files each
entry under its own `category` key.** A category the vault lacks is created with
`add_category`, in the order the imported entries first name it. Only entries actually
imported create one. `category=""` and a named category behave exactly as today.

`ImportPreviewDialog`'s *Add to category* picker gains a first option,
**"Categories from file"**, shown only when some parsed entry carries a non-empty category.
It is then the default and passes `category=None`. `_finish_import` passes `None` through
unchanged. Its existing fallback, which resets a category deleted while the preview was open,
applies only to a named category.

### 4.5 The mapping step

A file with no single recognised format opens `CsvMappingDialog` before the preview. It shows
one row per column, with the header text and the first non-empty value under it. Each row
has a choice: **Name, Category, Notes, Field, Skip**.

- Defaults, matched on the casefolded header: `name` or `title` → Name; `category`, `folder`
  or `group` → Category; `notes`, `note` or `comments` → Notes; everything else → Field.
- *Continue* is enabled only when exactly one column is Name and at most one each is Category
  and Notes. Otherwise a status line says which rule is unmet.
- A Field column's label is its header text. Sensitivity follows § 4.3's rule.

```python
def entries_from_mapping(header: list[str], rows: list[list[str]],
                         mapping: list[str]) -> list[dict]:   # one target per column
```

*Continue* builds the entries, applies the `MAX_IMPORT_ENTRIES` check, and opens
`ImportPreviewDialog`. *Cancel* imports nothing.

### 4.6 Menu and flow

Two new actions sit in the main menu beside their text-file siblings:

- **"Import from CSV file..."** opens `make_file_dialog` with a `*.csv` filter, in the home
  directory (INV-4's rule). Its outcomes are: `read_csv_file`, then `detect_csv_format`, then
  either `csv_rows_to_entries` or the mapping step, then the existing empty, too-many and
  preview paths of `_on_import_file_chosen`.
- **"Export as CSV (decrypted)..."** confirms through `_confirm_destructive`, as INV-14 does.
  The body says the file is unencrypted. The default name is
  `rolodex_export_<YYYYMMDD>_<HHMMSS>.csv`.

### 4.7 Writing the export

```python
def export_csv(vault: dict) -> str:
```

- Header `name,category,notes,created,modified`, then `field_N_label,field_N_value,field_N_hidden`
  for N = 1 to the largest field count in the vault.
- One row per entry, in `list_entries` order. Shorter entries are padded with empty cells.
- `category` is `entry_category(vault, entry)`, so a category the vault no longer lists exports
  as empty, which is what the sidebar shows.
- `hidden` is `yes` or `no`, from the stored `sensitive` flag.
- Written by `csv.writer` with minimal quoting and `\r\n` line ends, as RFC 4180 specifies.
  The text is encoded as UTF-8 without a byte-order mark and written through
  `write_private_file`. `import-export-backup.md` INV-15 and INV-15a therefore apply unchanged.

## 5. Invariants

The test clauses are test-file paths: the code does not exist yet, so nothing runs today.
Each fixture names the rule it isolates.

- **INV-1** — Exporting a vault with `export_csv` and importing the result with
  `read_csv_file` → `csv_rows_to_entries` yields entries equal to the originals. Equal means
  the same name, category, notes, created and modified, and fields equal in order, label,
  value and `sensitive`. The fixture holds a multi-line note, a value with `,` and `"`, a
  leading `=`, a non-ASCII name, a field hidden under a keyword-free label, and entries with
  zero and with three fields.
  *Test:* `tests/test_csv.py::test_INV1_rolodex_csv_round_trips`.
  *Breaks when:* the hidden column, the timestamps or the padding are dropped, or the file is
  read without `newline=""`. The keyword-free hidden field is the one only the hidden column
  can restore.

- **INV-2** — `detect_csv_format` returns each format's id for that writer's header, both the
  current header and the older one § 4.3's sources name (Bitwarden without `archivedDate`,
  KeePassXC without its last four columns, Chromium without `note`). It returns the same id
  whatever the case and with surrounding spaces.
  *Test:* `tests/test_csv.py::test_INV2_detects_each_writer_header`, a table of literal headers.
  *Breaks when:* detection compares whole header lines, or compares case-sensitively.

- **INV-3** — A header that two formats would match, or none, returns `None`.
  *Test:* `tests/test_csv.py::test_INV3_ambiguous_or_unknown_header_is_none`. The fixture is
  KeePassXC's required columns plus `otpauth`, which satisfies both `keepassxc` and
  `1password`.
  *Breaks when:* detection returns the first match in some order.

- **INV-4** — Each format's mapping follows § 4.3's table, including KeePassXC's root segment
  removed under a root that is not `Root`, Bitwarden's `fields` lines, and Firefox's host
  name.
  *Test:* `tests/test_csv.py::test_INV4_<format>_maps_columns`, one per format. The KeePassXC
  fixture uses the root `Passwords`.
  *Breaks when:* a column lands in the wrong target, or the root is stripped by matching the
  literal `Root/`.

- **INV-5** — A column the format's table does not name becomes a field labelled with its
  header text. It is not dropped.
  *Test:* `tests/test_csv.py::test_INV5_unknown_column_becomes_field`, a Chromium header plus
  a `Recovery email` column.
  *Breaks when:* the mapper iterates the table instead of the header.

- **INV-6** — A TOTP-mapped field is sensitive even when its value is not a valid seed.
  *Test:* `tests/test_csv.py::test_INV6_totp_field_always_sensitive`, with the value
  `not-a-seed`. The label `TOTP` matches no `SENSITIVE_KEYWORDS` member, and
  `parse_totp_field` rejects the value, so only this rule hides it.
  *Breaks when:* sensitivity uses only the `parse_text_file` rule.

- **INV-7** — A row longer than the header raises `ValueError`, and an oversized file raises
  `ValueError` with `_IMPORT_TOO_LARGE`. A cell over `csv`'s
  field-size limit raises `ValueError`, not `csv.Error`.
  *Test:* `tests/test_csv.py::test_INV7_malformed_file_raises_value_error`.
  *Breaks when:* extra cells are silently dropped, or `csv.Error` escapes into the GTK callback.

- **INV-8** — `import_entries(..., category=None)` files each entry under its own category.
  It creates a missing category once, in first-named order, only for imported entries.
  *Test:* `tests/test_csv.py::test_INV8_categories_from_file`. Two entries name `Work`, a
  skipped duplicate names `Old`, and the vault lacks both. Expect `Work` created once and `Old`
  not created.
  *Breaks when:* categories are created before the duplicate filter, or once per entry.

- **INV-9** — The preview offers "Categories from file" exactly when a parsed entry has a
  non-empty category, and then selects it by default.
  *Test:* `tests/test_widgets.py::test_INV9_csv_preview_category_option`, run under Xvfb as
  `CLAUDE.md` § Conventions describes.
  *Breaks when:* the option shows for a text-file import, or does not default.

- **INV-10** — The mapping step's *Continue* is sensitive only when exactly one column is
  Name and at most one each is Category and Notes. The defaults follow § 4.5.
  *Test:* `tests/test_widgets.py::test_INV10_mapping_dialog_gates_continue`.
  *Breaks when:* two Name columns are accepted, or none.

- **INV-11 (trust boundary)** — An imported CSV is untrusted input. It reaches the vault only
  as strings in the parsed-entry shape. It reaches the screen only through the existing
  escaping in the preview row title and the detail view. The file-size cap and the
  `MAX_IMPORT_ENTRIES` cap both apply before any dialog is built.
  *Test:* `tests/test_csv.py::test_INV11_caps`, where 2001 rows hit the entry cap; and
  `tests/test_widgets.py::test_INV11_csv_markup_shown_as_text`, where a name and a mapping-step
  header of `<b>x</b>` render as those literal characters.
  *Breaks when:* a new label or subtitle in either dialog skips `GLib.markup_escape_text`.

- **INV-12** — The CSV export is written through `write_private_file` and is `0600`, on first
  write and on overwrite.
  *Test:* `tests/test_csv.py::test_INV12_export_is_private`.
  *Breaks when:* the export uses `open(path, "w")`.

## 6. Failure modes

- **A non-UTF-8 file** (a spreadsheet saving as Windows-1252) raises `UnicodeDecodeError` and
  shows "Import Error" through the existing handler. No guessing of encodings.
- **A semicolon-delimited file**, which European spreadsheet locales write, reads as one
  column and lands in the mapping step. The user sees the problem there.
- **A headerless file** loses its first row to the header. The mapping step shows it as
  column names, which is visible.
- **Chromium duplicates** a credential shared across domains, one row per domain. The preview's
  duplicate marking catches same-named rows (`import-export-backup.md` INV-6).
- **KeePassXC's Recycle Bin** is exported too, and its entries import under a
  `Recycle Bin` category. They are visible in the preview and can be unticked.
- **Bitwarden `login_uri`** may hold several addresses joined in one cell. The cell becomes one
  URL field, as written.
- **Formulas in the export.** A value starting with `=`, `+`, `-` or `@` is written as-is, and a
  spreadsheet may run it as a formula on open (§ 8).
- **Excel and the export.** Excel reads a UTF-8 file without a byte-order mark as its local
  code page, so non-ASCII text shows garbled. Rolodex's own import is unaffected.

## 7. Tests

`tests/test_csv.py` is new and covers INV-1, INV-2, INV-3, INV-4, INV-5, INV-6, INV-7,
INV-8, INV-11 (the caps) and INV-12 against the pure layer. Its fixtures are literal strings
written to a `tmp_path`. `tests/test_widgets.py` gains INV-9, INV-10 and INV-11 (the markup). Each test is seen failing before its code lands, by running it against the tree
without the function or rule it names.

## 8. Alternatives considered (and rejected)

- **Common columns with extras in notes**, rejected by the user (§ 3): other apps import it
  directly, but it turns a field into notes text on the way back.
- **One row per field**, rejected by the user (§ 3): nothing else can read it.
- **Formula escaping on export** (OWASP: prefix `'` to a cell starting with `=`, `+`, `-`, `@`,
  tab or CR; Source: <https://community.owasp.org/attacks/CSV_Injection>). Rejected: it
  changes the data, so re-import cannot tell an escape from a value that really starts with
  `'`, which breaks INV-1. OWASP also notes the protection does not survive a spreadsheet
  save and reopen. None of the five writers escapes either, so an imported password may
  legitimately start with `=`. The file is already a plaintext credential dump behind a
  confirmation.
- **Precedence order between formats**, rejected: it makes the result depend on table order,
  and an ambiguous header is better shown to the user (INV-3).
- **Delimiter sniffing** (`csv.Sniffer`), rejected: it guesses wrong on short files, and a
  wrong guess passes silently, where a wrong delimiter fails visibly (§ 6).
- **Foreign timestamps** (KeePassXC `Created`, Firefox `timeCreated`), rejected: each uses
  its own unit and zone rule, and a wrong conversion is silent. Foreign entries get fresh
  timestamps.

## 9. Out of scope

- 1Password 7's per-platform CSV layouts — deferred; not yet queued.
- Exporting in another manager's layout — deferred; not yet queued.
- Semicolon or tab delimiters — deferred; not yet queued.

## 10. What checks this

| Rule | What catches a breach |
|------|----------------------|
| INV-1 | `tests/test_csv.py::test_INV1_rolodex_csv_round_trips` |
| INV-2 | `tests/test_csv.py::test_INV2_detects_each_writer_header` |
| INV-3 | `tests/test_csv.py::test_INV3_ambiguous_or_unknown_header_is_none` |
| INV-4 | `tests/test_csv.py::test_INV4_<format>_maps_columns` |
| INV-5 | `tests/test_csv.py::test_INV5_unknown_column_becomes_field` |
| INV-6 | `tests/test_csv.py::test_INV6_totp_field_always_sensitive` |
| INV-7 | `tests/test_csv.py::test_INV7_malformed_file_raises_value_error` |
| INV-8 | `tests/test_csv.py::test_INV8_categories_from_file` |
| INV-9 | `tests/test_widgets.py::test_INV9_csv_preview_category_option` |
| INV-10 | `tests/test_widgets.py::test_INV10_mapping_dialog_gates_continue` |
| INV-11 | Partial: `tests/test_csv.py::test_INV11_caps` and `tests/test_widgets.py::test_INV11_csv_markup_shown_as_text` cover the caps and today's labels; **nothing** checks a label added to either dialog later |
| INV-12 | `tests/test_csv.py::test_INV12_export_is_private` |
| § 4.3 headers still match the live writers | **nothing** — a writer can change its header in any release; INV-2 pins only the headers known on 2026-11-05 |

## 11. Cross-doc impact

- `docs/specs/import-export-backup.md`: annotate INV-7a and INV-8 with `category=None` and the
  "Categories from file" option, and point its Notes line at this spec.
- `docs/specs/README.md`: a row for this spec.
- `CHANGELOG.md` and `README.md`: the two menu items.

## 12. Cold-eyes loop log

Rows live in `../reviews/ROLO-0012-csv-import-export-loop-log.md`.

## 13. Migration / compatibility

The vault's on-disk shape does not change. The parsed-entry dict gains optional keys that
`import_entries` reads with `.get`, so `parse_text_file`'s output imports as before.
