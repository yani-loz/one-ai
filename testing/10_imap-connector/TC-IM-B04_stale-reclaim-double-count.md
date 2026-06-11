| ID · Suite · Type · Mode | TC-IM-B04 · B (sync lifecycle) · Concurrency · runner |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ⚠️ Pass-with-concern | 🆕 NEW | Low | Executed |

## Objective
Test whether the stale-claim window (`STALE_SECONDS=300`, `connector_sync_runner.py:62`) allows a second runner to reclaim a still-live run and DOUBLE-COUNT the same mail. The break hypothesis predicted a NEW double-count defect; the honest measurement (count `email_message` rows) was left to decide.

## Break hypothesis
If `claim_for_sync` reclaims a claim whose heartbeat is older than 300 s while the original runner is still alive (a long batch / a paused process whose heartbeat ticker stalled), two runners process the same UIDs concurrently and the mailbox is ingested twice.

## Steps
1. Seed a connection; claim with run A; run A ingests a 2-message batch (emails + cursor committed).
2. Age the heartbeat 10 min past the stale window; reclaim with run B; run B re-streams the SAME 2 UIDs.
3. Count `email_message` rows for the org after run A and after run B.

## Expected (corrected from the catalog's "🆕 double-count")
The defence holds: re-ingest of the same `dedup_key` collides with `uq_email_message_dedup` → SKIPPED (and run A's fenced writes would fail the moment B steals the claim). Email count stays at 2, NOT 4.

## Execution result (2026-06-09)
```
  [PASS] B04_stale_reclaim_no_double_count :: reclaimed=True emails_after_runA=2 emails_after_runB=2 (double-count would be 4)
```

**Verdict:** ⚠️ Pass-with-concern. The predicted double-count did NOT reproduce — the reclaim succeeded (as designed for crash recovery) but the dedup unique constraint made the re-ingest idempotent, so the mail was counted once (2, not 4). Two layers protect this: (a) the fence — once run B reclaims, run A's next `set_synced_count` returns 0 rows and run A aborts its batch; (b) dedup — any overlapping ingest collides on `uq_email_message_dedup` → SKIPPED. **Tag: 🆕 NEW, Low** for the residual concern only: if `STALE_SECONDS` is ever set below a real batch's wall-time, a *still-live* run can be reclaimed (wasted re-fetch work + a confusing two-runner window), and the heartbeat ticker dying mid-batch relies on `set_synced_count`'s belt-and-suspenders heartbeat refresh to stay fresh. No mail loss, no double-count, no isolation breach. Not a data-integrity defect.
