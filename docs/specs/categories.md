# Spec: Categories

Retroactive spec for category grouping (`add_category`, `rename_category`, `delete_category`,
`entries_by_category`, `CategoryHeaderRow`, `ManageCategoriesDialog`, `MainWindow._refresh_list`).

## Behaviour

### Model

- **INV-1** `vault["categories"]` is an ordered list of unique names. `add_category` returns
  `False` and does nothing if the name already exists.
- **INV-2** An entry's membership is the string `entry["category"]`; `""` means uncategorised.
- **INV-3** An entry whose `category` names a category not in the list is treated as
  uncategorised for display (`entries_by_category` folds unknown categories into `""`).

### Rename & delete

- **INV-4** `rename_category(old, new)` renames the list entry in place (preserving its
  position) and updates every entry with `category == old` to `new`.
- **INV-5** `delete_category(name)` removes it from the list and sets every entry with that
  category back to `""` (entries are never deleted with the category). The UI confirms first
  and states how many entries will move to Uncategorised.
- **INV-6** Renaming/deleting a category updates the collapsed-state set accordingly (a renamed
  collapsed category stays collapsed under its new name).

### Sidebar grouping

- **INV-7** With no categories defined, the sidebar is a flat alphabetical list.
- **INV-8** With categories defined and no active search, the sidebar shows a
  `CategoryHeaderRow` per category in list order, each followed by its entries sorted by name;
  an "Uncategorised" group is shown last, only if it is non-empty.
- **INV-9** Category headers are non-selectable but activatable; activating one toggles collapse
  for that category. Collapsed state is session-only (not persisted to disk).
- **INV-10** Each header shows the category name (uppercased, "Uncategorised" for `""`), a
  disclosure arrow reflecting collapsed state, and a count badge of its entries.

### Moving entries

- **INV-11** An entry can be moved to a category by dragging its row onto a category header, or
  via the right-click "Move to…" context menu (only present when categories exist).
- **INV-12** Moving an entry sets its `category`, updates `modified`, saves the vault, and
  refreshes; the "Move to…" menu omits the entry's current category and offers "Uncategorised"
  only when the entry is currently in a category.

### Management dialog

- **INV-13** `ManageCategoriesDialog` lists categories with per-row rename and delete, an add
  field, and drag-to-reorder; reordering rewrites `vault["categories"]` order and saves.

## Notes

- Category order is user-meaningful and drives sidebar order; entry order within a group is
  always by name and not user-controllable.
- A category filter control is roadmap ROLO-0009.
