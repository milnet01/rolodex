# Contributing to Rolodex

Thanks for your interest. Rolodex is a small, deliberately simple project — one Python file,
no build step, no framework beyond GTK 4 / libadwaita. Contributions that keep it that way
are the most welcome.

## Ground rules

- **Never commit real credential data.** `contacts.vault`, `*.vault`, `.rolodex.conf`, and
  export files are git-ignored. Double-check `git status` before every commit. If you need a
  vault for testing, create a throwaway one with a junk password.
- **Keep it one file.** `rolodex.py` is the whole app on purpose. Don't split it into a
  package or add a dependency without a strong reason discussed in an issue first.
- **The pure-logic layer stays GTK-free.** Everything above the `GTK4 / Adwaita GUI` banner
  (encryption, data ops, parsing) must not import `gi`. That boundary is what keeps the core
  testable and reviewable.

## Standards

Before writing code or docs, read the relevant standard in [`docs/`](docs/):

- [`docs/coding-standards.md`](docs/coding-standards.md)
- [`docs/file-naming.md`](docs/file-naming.md)
- [`docs/documentation-standards.md`](docs/documentation-standards.md)
- [`docs/security-standards.md`](docs/security-standards.md)

## Development setup

There is nothing to build. Install the runtime dependencies (see the README), then:

```bash
python3 rolodex.py
```

Use a scratch vault, not your real one, while developing.

## Making a change

1. Open an issue describing the change first for anything non-trivial — it's cheaper to
   agree on the approach before code exists.
2. Branch from `main`: `git checkout -b <topic>`.
3. Make the change. Keep the diff scoped to one concern.
4. Manually exercise the affected flow end to end (there is no automated test suite yet — see
   the roadmap). At minimum: create a vault, add/edit/delete an entry, lock and re-unlock.
5. Update `CHANGELOG.md` under an *Unreleased* heading.
6. Open a pull request describing what changed and how you verified it.

## Commit messages

- One logical change per commit.
- Imperative present tense: "Add category export", not "Added" / "Adds".
- Explain the *why* in the body when it isn't obvious from the diff.

## Reporting bugs & security issues

- Ordinary bugs: open a GitHub issue with steps to reproduce.
- Security vulnerabilities: **do not** use public issues — follow [`SECURITY.md`](SECURITY.md).
