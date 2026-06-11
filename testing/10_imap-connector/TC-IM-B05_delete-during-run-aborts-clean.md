| ID · Suite · Type · Mode | TC-IM-B05 · B (sync lifecycle) · Adversarial · runner |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ✅ Pass | — (positive/contract) | Info | Executed |

## Objective
Deleting a connection during an active sync must abort the runner cleanly — `ON DELETE CASCADE` purges the email/cursor/run rows, and the runner's next fenced write hits 0 rows (or `get_in_org` returns None) and aborts without an unhandled crash or orphaned rows.

## Break hypothesis
If the runner held a detached ORM row or wrote children (emails/cursor) after the parent connection was deleted, it could either crash with an FK error or leave orphaned rows. The defence: (1) `connector_sync_cursor.connection_id` and `connector_sync_run.connection_id` are `ON DELETE CASCADE` (migration 0011:108,137); `email_message.connection_id` likewise (migration 0008:201). (2) The runner carries only plain UUIDs across commits and re-reads via `get_in_org` (`connector_sync_runner.py:166-168` returns early if None); every progress write is fenced.

## Steps
1. Seed a connection; claim run; commit. Stream batch 1, then PAUSE the runner mid-stream (a gated fake connector blocks before batch 2).
2. While paused, `DELETE` the connection (CASCADE removes the batch-1 email + cursor + run rows).
3. Resume the runner; let it attempt batch 2 + finalize.
4. Assert: `run()` returned without raising; zero orphan emails; zero orphan cursors; zero connection rows.

## Expected
`run()` completes cleanly (it never raises by contract); no orphan email/cursor rows survive the delete.

## Execution result (2026-06-09)
```
  [PASS] B05_delete_during_run_aborts_clean :: run_clean=True orphan_emails=0 orphan_cursors=0 connection_rows=0
```

**Verdict:** ✅ PASS. The CASCADE purged batch-1's email + cursor rows with the connection, the resumed runner's fenced writes / `get_in_org` re-read hit nothing and it aborted cleanly — `run()` returned with no exception, no orphans. **Tag: — (positive/contract).** The delete-during-run path is safe.
