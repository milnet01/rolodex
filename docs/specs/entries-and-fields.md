# Spec: Entries & Fields

Retroactive spec for the entry data model and the add/edit/delete/detail flows
(`add_entry`, `update_entry`, `delete_entry`, `is_sensitive_label`, `field_category`,
`AddEditDialog`, `FieldRow`, `MainWindow._show_detail`).

## Behaviour

### Entry model

- **INV-1** An entry is keyed by a `uuid4` string and has: `name`, `category`, `fields`,
  `notes`, `created`, `modified`. Timestamps are ISO-8601 strings.
- **INV-2** `add_entry` sets `created` and `modified` to the same current time. Any mutation
  via `update_entry` or a category move updates `modified` only.
- **INV-3** A field is `{"label": str, "value": str, "sensitive": bool}`. Fields are an
  ordered list; their order is preserved as authored.

### Add / edit dialog

- **INV-4** A new entry's editor starts with two fields: "Username" (not sensitive) and
  "Password" (sensitive).
- **INV-5** On save, a field is kept only if its label OR value is non-empty; an empty label
  on a kept field becomes "Unlabeled".
- **INV-6** Saving with an empty entry name is a no-op (the dialog does not commit).
- **INV-7** Fields can be reordered by dragging the handle; the saved order matches the final
  visual order.
- **INV-8** The category selector defaults to "(None)" for a new entry and pre-selects the
  entry's current category when editing; "(None)" maps to `category = ""`.

### Sensitive masking

- **INV-9** A label is auto-classified sensitive when it contains any `SENSITIVE_KEYWORDS`
  token (password, pass, secret, key, token, pin, authenticator), case-insensitive. In the
  editor, changing a label live toggles the field's masked state and the "Hide" checkbox.
- **INV-10** The "Hide" checkbox is the source of truth for `sensitive` on save — the user can
  override the auto-detection either way.
- **INV-11** In the detail view a sensitive field shows the mask string (8 bullets) until
  *Reveal sensitive* is toggled; non-sensitive fields always show their value.
- **INV-12** Reveal state is per-entry and resets to hidden whenever the selected entry changes.

### Field colour classification

- **INV-13** `field_category(label)` maps a label to exactly one of
  `credential | key | identity | url | date | other` using `FIELD_CATEGORIES`, first match
  wins, case-insensitive substring match. Unmatched labels are `other`.
- **INV-14** This classification is presentational only (it selects the `.field-<category>`
  CSS border colour) and is independent of the `sensitive` flag and of the user's category.

### Detail view & delete

- **INV-15** The detail view shows name, fields (with per-field copy buttons), notes (only if
  present), Reveal/Edit/Delete actions, and created/modified timestamps.
- **INV-16** Copying a field puts its raw value on the clipboard and shows a "Copied <label>"
  toast; if no clipboard tool is available it shows "Clipboard not available".
- **INV-17** Delete requires confirmation via an `Adw.AlertDialog`; on confirm the entry is
  removed, the vault saved, the detail pane cleared, and the list refreshed.
- **INV-18** Entry `name` and field values are escaped with `GLib.markup_escape_text` before
  being placed into any GTK markup sink (e.g. `Adw.ActionRow` titles).

## Notes

- The three orthogonal axes — user `category`, cosmetic `field_category`, and `sensitive` —
  are deliberately independent (see `DESIGN.md`).
- Built-in password generation for sensitive fields is roadmap ROLO-0004.
