# History — CLAUDE.md

Genre: record
Status: active

Why the rules in `CLAUDE.md` say what they say. Nothing is built from
this file and nobody conforms to it; it exists so that the file every
session reads on every turn carries what is true now, and not the story
of how it got there.

## The architecture map

**The pure-logic / GUI split is found by grepping for the banner.** The
rule reads that way because `CLAUDE.md` used to cite a line number for
the boundary, and it drifted by hundreds of lines as the file grew. The
instruction that replaced it — grep for the banner, do not trust a line
number — is in `CLAUDE.md` and is what is true now.

## Moved out of CLAUDE.md, 2026-10-15

These clauses explained a rule's origin and changed nothing a session
does. They left the always-loaded file to cut the tokens every request
re-reads.

- **Broadway.** The warning against `GDK_BACKEND=broadway` exists because
  a whole verify-delivery run was thrown away to its false "display
  works" probe. The in-process `MainWindow` route is how the end-to-end
  feature checks were done.
- **The release-signing key** became real in ROLO-0041 (2026-09-21). It
  replaced a 32-zero placeholder that left the updater failing closed:
  able to offer an update and never install one. The INV-11 test pins it
  against a revert to that placeholder.
- **The import file picker** opens in the user's home directory
  (`GLib.get_home_dir()`). The note that it has no hardcoded import path
  recorded the removal of one.
- **Privileged commands.** The full `SUDO_ASKPASS` recipe lives in
  `/mnt/Games/CLAUDE.md`, which loads for this project; `CLAUDE.md`
  keeps a pointer instead of a copy.
