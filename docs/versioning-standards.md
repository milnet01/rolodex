# Versioning Standards

## Policy: Semantic Versioning

Rolodex versions are **`MAJOR.MINOR.PATCH`** (e.g. `1.4.0`), following
[Semantic Versioning 2.0.0](https://semver.org/). Reading the three numbers left to right:

```
   1  .  4  .  0
   │     │     │
   │     │     └── PATCH — bug fixes only, nothing new, nothing broken
   │     └──────── MINOR — new features, everything old still works
   └────────────── MAJOR — a breaking change (old vaults / configs / callers stop working)
```

Bump exactly one of the three for a release, and **reset the ones to its right to `0`**:

| Change | Example | Next version from `1.4.2` |
|--------|---------|---------------------------|
| Breaking change | vault format old versions can't read | `2.0.0` |
| New feature (backward-compatible) | add TOTP codes, a new menu item | `1.5.0` |
| Bug / security / packaging fix | fix a crash, patch a dependency | `1.4.3` |

That is the whole rule. The rest of this document is **what counts as each level for Rolodex**,
because "is this breaking?" is the only judgement call and the vault file is where it usually lives.

## What counts as each level

### MAJOR — bump the first number

A change is **breaking** if a user (or an integrator) who upgrades has something that *used to
work* stop working. For Rolodex that means any of:

- **The on-disk vault format changes so an older app can no longer open a newer vault.**
  Adding fields that `migrate_vault()` fills in is *not* breaking (see MINOR). Changing the
  magic bytes, the KDF, the cipher, or the container layout in `docs/specs/vault-format-and-crypto.md`
  such that a downgrade fails **is** breaking.
- **A documented config key or file location is removed or given incompatible meaning** —
  e.g. renaming a `.rolodex.conf` key, or changing what an existing key's value means.
- **A supported platform is dropped** — e.g. ceasing to ship one of the three OS binaries, or
  raising the minimum GTK / Python version above what a supported distro provides.

Breaking releases get a `### Changed` or `### Removed` CHANGELOG entry that says, in plain words,
what a user must do (e.g. "export before upgrading; v1 vaults are read-only after this").

### MINOR — bump the middle number

New, **backward-compatible** capability. After the upgrade everything a user did before still
works; there's simply *more*. This is the common case. Examples (all real v1.3.0 work):

- A new feature: TOTP codes, the password-health report, keyboard shortcuts.
- A new **additive** vault field that `migrate_vault()` backfills idempotently, where an
  untouched older vault still loads unchanged.
- A new optional `.rolodex.conf` preference with a safe default when absent.

### PATCH — bump the last number

A fix with **no new feature surface and no format change**:

- Bug fixes and crash fixes.
- **Security fixes that only fix** (no new user-facing feature). A security change that *adds*
  a feature — e.g. the auto-lock and clipboard-clear work — is a MINOR, not a PATCH.
- Dependency bumps with no behavioural change (see `docs/dependency-management-standards.md`).
- Packaging / build / CI / docs-only fixes that still warrant a release.

When unsure between MINOR and PATCH, ask "did I add something a user can now do that they
couldn't before?" — yes ⇒ MINOR, no ⇒ PATCH. When unsure between MAJOR and MINOR, ask "can an
older Rolodex still open a vault this version wrote?" — no ⇒ MAJOR.

## Where the version lives, and how a release is cut

- **Source of truth:** the topmost dated heading in `CHANGELOG.md` (`## [X.Y.Z] - DATE`). There is
  no `__version__` string in the single-file app — the tag and the changelog carry the version.
- **During development:** entries accumulate under `## [Unreleased]`. The category an entry lands
  in (`Added` / `Changed` / `Fixed` / `Security`) is what tells you the release level: any `Added`
  ⇒ at least MINOR; a breaking `Changed`/`Removed` ⇒ MAJOR; only fixes ⇒ PATCH.
- **Cutting the release** is mechanical — `/bump <major|minor|patch>` (or an explicit `X.Y.Z`)
  runs the recipe in `.claude/bump.json`: it dates the `[Unreleased]` block into a `[X.Y.Z]`
  section and advances the compare-link footer. `/release` wraps that with lint + tests + commit
  + tag + GitHub Release.
- **Tags are always `vX.Y.Z`** (annotated). Pushing a `v*` tag is what triggers
  `.github/workflows/build.yml` to build and attach the Linux / Windows / macOS binaries.
  One tag per release; tags are never moved or force-pushed.

## Pre-1.0 note

Rolodex is past `1.0.0`, so the full rule above applies. For completeness: before `1.0.0`, SemVer
treats the API as unstable and allows breaking changes in MINOR bumps (`0.y.z`). That phase is
over — from `1.0.0` onward, a breaking change **must** bump MAJOR.
