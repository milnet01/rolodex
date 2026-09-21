# Gate log

Genre: record

One row per review run. A commit citing a gate cites a run id here. The
gate-record hook checks the id is in this file, so the file exists from
the first commit.

This index starts empty and is not back-filled. Rolodex's earlier reviews
are recorded as `docs/review-<date>-<document>.md`, one dated record per
reviewed document, each carrying its own loop log; those stay where they
are and remain the account of what those runs found. A row invented here
for a run nobody watched would be the one thing this index must not
carry.

| Run | Date | Subject | Genre | Pass | Lanes | Kept | Dismissed | Outcome |
|---|---|---|---|---|---|---|---|---|
