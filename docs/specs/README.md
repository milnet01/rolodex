# Feature Specs

These are **retroactive** specifications: they document how Rolodex *already* behaves, feature
by feature, extracted from the shipped code. They exist so that future changes have a written
contract to check against, and so a reviewer can tell intended behaviour from an accident.

Rolodex is a small app, so this set is small.

| Spec | Covers |
|------|--------|
| [vault-format-and-crypto.md](vault-format-and-crypto.md) | On-disk file format, key derivation, encryption, file permissions, migration |
| [entries-and-fields.md](entries-and-fields.md) | Entry data model, fields, sensitive masking, field colour classification, add/edit/delete |
| [categories.md](categories.md) | Category model, grouped sidebar, drag-and-drop, management dialog |
| [search.md](search.md) | Sidebar search behaviour and match rules |
| [import-export-backup.md](import-export-backup.md) | Text import + preview, encrypted backup/restore, plaintext export |
| [master-password.md](master-password.md) | Create, unlock, and change the master password |

## Spec format

Each spec states its **Behaviour** as numbered invariants (INV-n) — testable statements of
what must be true — followed by **Notes** on rationale or edge cases. An invariant is the unit
a test or a reviewer checks. When behaviour changes, update the affected INV in the same commit
and reflect it in `CHANGELOG.md`.

New specs for planned work (see `ROADMAP.md`) are written *before* implementation and should
be run through the `/cold-eyes` review before coding begins.
