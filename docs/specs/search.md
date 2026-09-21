# Spec: Search

Genre: spec
Status: active
Describes: current

Retroactive spec for sidebar search (`search_entries`, `MainWindow._on_search_changed`,
`MainWindow._refresh_list`).

## Behaviour

- **INV-1** Search is case-insensitive. The query is split into words at whitespace (ROLO-0009).
- **INV-2** An entry matches when **every** word is a substring of at least one of: its `name`,
  its `category`, any field `label`, any field `value`, or its `notes`. The words may match in
  different places and in any order, so a one-word query is plain substring matching.
- **INV-3** Field *values* are searched — including sensitive ones — so a user can find an entry
  by a known password even though the value is masked in the detail view.
- **INV-4** Results are returned sorted by entry name, case-insensitive.
- **INV-5** While a query or a category filter is active the sidebar shows a flat list of
  matches with **no category grouping**, regardless of whether categories exist.
- **INV-5a** When the vault has categories, a drop-down above the list offers "All categories",
  each category, and "Uncategorised" (which includes an entry naming a category the vault no
  longer lists). It narrows the list, and any active query, to that category. It is hidden when
  there are no categories, and falls back to "All categories" if the chosen one is deleted.
- **INV-6** The count label reads "`N` of `M` entries" while a search or a filter is active
  (matches out of total); with no active query it reads "`M` entries". The noun is singularised — "entry" for
  exactly one, else "entries" (via `entries_noun`), agreeing with `M`.
- **INV-7** Clearing the search restores the normal view (grouped if categories exist, else
  flat) and re-selects the current entry if it is still visible. The query is stripped first, so
  a whitespace-only query counts as no active search (normal view).

## Notes

- Matching stops at the first matching field per entry (no duplicate rows).
- `search_entries(vault, query, category=None)` is a pure function, covered in
  `tests/test_vault.py` and `tests/test_regressions.py`.
