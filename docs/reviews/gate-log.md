# Gate log

Genre: record

One row per review run. A commit citing a gate cites a run id here.

Nothing checks that a cited id is in this file. Adoption installed a
commit-msg hook delegating to `~/.claude/hooks/gate-record`, which never
existed there, so that check never ran. The hook was replaced on
2026-09-25 with the skeleton's `.githooks/commit-msg`, which checks
subject shape only.

This index starts empty and is not back-filled. Rolodex's earlier reviews
are recorded as `docs/review-<date>-<document>.md`, one dated record per
reviewed document, each carrying its own loop log; those stay where they
are and remain the account of what those runs found. A row invented here
for a run nobody watched would be the one thing this index must not
carry.

| Run | Date | Subject | Genre | Pass | Lanes | Kept | Dismissed | Outcome |
|---|---|---|---|---|---|---|---|---|
