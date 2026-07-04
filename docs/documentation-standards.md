# Documentation Standards

## What must stay current

When a change touches these areas, update the docs in the *same* commit:

| Change | Update |
|--------|--------|
| Any user-visible behaviour | `README.md` + `CHANGELOG.md` |
| On-disk vault format / schema | `README.md` (format section), `SECURITY.md`, `CLAUDE.md`, `migrate_vault()` |
| Crypto, permissions, or threat surface | `SECURITY.md` |
| New convention or rule for contributors | the relevant `docs/*.md` standard |
| A shipped release | `CHANGELOG.md` (move *Unreleased* → version) |
| Completed or reprioritised roadmap item | `ROADMAP.md` |

Documentation that lies is worse than none. A stale doc that contradicts the code should be
treated as a bug.

## The document set

- **README.md** — what the app is, install, run, security summary, file layout. First contact
  for a new user. Keep it skimmable.
- **CHANGELOG.md** — [Keep a Changelog](https://keepachangelog.com/) format, newest first,
  grouped by Added / Changed / Fixed / Removed / Security. Every user-facing change lands here.
- **SECURITY.md** — threat model, cryptographic design, and private reporting process.
- **CONTRIBUTING.md** — how to set up, the ground rules, and the PR flow.
- **ROADMAP.md** — planned work, grouped by priority, each item spec-ready.
- **CLAUDE.md** — orientation for AI coding assistants; the big-picture architecture that
  spans the file. Mirror any architectural change here.
- **docs/*.md** — the standards (this set). One concern per file, kebab-case names.

## Style

- Write in plain, direct English. Short sentences, short paragraphs.
- Lead with what a thing does for the reader before how it works internally.
- Prefer a small table or a worked example over a wall of prose.
- Use fenced code blocks with a language tag for anything runnable.
- American spelling in prose is fine; match existing surrounding style within a file.

## Code-level documentation

- Module docstring at the top of `rolodex.py` states what the app is in one line.
- Public pure-logic functions get a one-line docstring when the name isn't self-explanatory
  (e.g. `migrate_vault`, `entries_by_category`, `field_category`).
- Section banner comments (`# ---- Encryption layer ----`) mark the major regions of the file
  and, critically, the pure-logic ⁄ GUI boundary. Keep them.
- Inline comments explain non-obvious *why*, per the coding standard — not narration.
