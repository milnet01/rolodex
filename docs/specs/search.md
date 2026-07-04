# Spec: Search

Retroactive spec for sidebar search (`search_entries`, `MainWindow._on_search_changed`,
`MainWindow._refresh_list`).

## Behaviour

- **INV-1** Search is case-insensitive substring matching on the query.
- **INV-2** An entry matches if the query is a substring of any of: its `name`, its `category`,
  any field `label`, any field `value`, or its `notes`. A single match includes the entry.
- **INV-3** Field *values* are searched — including sensitive ones — so a user can find an entry
  by a known password even though the value is masked in the detail view.
- **INV-4** Results are returned sorted by entry name, case-insensitive.
- **INV-5** While a query is active the sidebar shows a flat list of matches with **no category
  grouping**, regardless of whether categories exist.
- **INV-6** The count label reads "`N` of `M` entries" during an active search (matches out of
  total); with no active query it reads "`M` entries". The noun is singularised — "entry" for
  exactly one, else "entries" (via `entries_noun`), agreeing with `M`.
- **INV-7** Clearing the search restores the normal view (grouped if categories exist, else
  flat) and re-selects the current entry if it is still visible. The query is stripped first, so
  a whitespace-only query counts as no active search (normal view).

## Notes

- Matching stops at the first matching field per entry (no duplicate rows).
- Fuzzy/token matching and a category filter are roadmap ROLO-0009; `search_entries` is a pure
  function and is a primary target of the ROLO-0001 test suite.
