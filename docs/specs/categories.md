# Spec: Categories

Retroactive spec for category grouping (`add_category`, `rename_category`, `delete_category`,
`entries_by_category`, `CategoryHeaderRow`, `ManageCategoriesDialog`, `MainWindow._refresh_list`).

## Behaviour

### Model

- **INV-1** `vault["categories"]` is an ordered list of unique names. `add_category` returns
  `False` and does nothing if the name already exists — but it does **not** itself trim or
  reject empty/whitespace names; the non-empty guard lives in the UI add-path
  (`_add_category` strips and drops empty input). The empty string `""` is reserved for
  uncategorised (INV-2) and is not a valid category name.
- **INV-2** An entry's membership is the string `entry["category"]`; `""` means uncategorised.
- **INV-3** An entry whose `category` names a category not in the list is treated as
  uncategorised for display (`entries_by_category` folds unknown categories into `""`).

### Rename & delete

- **INV-4** `rename_category(old, new)` renames the list entry in place (preserving its
  position) and updates every entry with `category == old` to `new`. It does **not** bump those
  entries' `modified` timestamps (unlike a drag/right-click move — see `entries-and-fields.md`
  INV-2).
- **INV-5** `delete_category(name)` removes it from the list and sets every entry with that
  category back to `""` (entries are never deleted with the category), and likewise does **not**
  bump their `modified`. The UI confirms first and states how many entries will move to
  Uncategorised.
- **INV-6** Renaming/deleting a category updates the collapsed-state set accordingly (a renamed
  collapsed category stays collapsed under its new name).

### Sidebar grouping

- **INV-7** With no categories defined, the sidebar is a flat alphabetical list.
- **INV-8** With categories defined and no active search, the sidebar shows a
  `CategoryHeaderRow` per category in list order, each followed by its entries sorted by name; a
  defined category renders its header even when empty (count 0). An "Uncategorised" group is
  shown last, and only if it is non-empty (this is the one group suppressed when empty).
- **INV-9** Category headers are non-selectable but activatable; activating one toggles collapse
  for that category. Collapsed state is session-only (not persisted to disk).
- **INV-10** Each header shows the category name, a disclosure arrow reflecting collapsed
  state, and a count badge of its entries. The name is uppercased for display (`.upper()`), so
  the fallback label for `""` renders as `UNCATEGORISED`.

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
- Because the Uncategorised group is only rendered when non-empty (INV-8), there is no drag
  target for it while it is empty; the right-click "Move to… → Uncategorised" (INV-12) covers
  that case.
- A category filter control is roadmap ROLO-0009.
