| ID · Suite · Type · Mode | TC-IM-B09 · B (sync lifecycle) · Boundary · runner |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ✅ Pass (defect reproduced) | 🆕 NEW | Low | Executed |

## Objective
The `failed_uids` ARRAY on `connector_sync_cursor` grows without bound across runs — it is cleared ONLY by a UIDVALIDITY reset (which restarts the tracker at floor 0). No cap, no pruning below the cursor floor.

## Break hypothesis
Every permanently-failed UID is appended to `failed_uids` and re-persisted each batch (`connector_sync_runner.py:275` writes `sorted(tracker.failed)`; `_FolderTracker.from_state` re-seeds it from the cursor every run). Nothing ever removes an entry except a UIDVALIDITY change (`_ingest_batch:254-256` builds a fresh tracker at floor 0, dropping the old failed set). A folder that accrues poison mail over months grows a monotonically-increasing array re-read and re-written on every batch — unbounded row growth + per-batch I/O on an ever-larger array.

## Steps (prove once)
1. Simulate 50 runs each persisting one more permanently-failed UID under the SAME generation (uidvalidity 100), via direct cursor UPSERTs.
2. Assert the stored `failed_uids` has length 50 (no cap applied).
3. UPSERT a UIDVALIDITY reset (200, floor 0, empty failed) and assert the array drops to 0 — the only clearing path.

## Expected
`len(failed_uids)==50` after 50 runs; `==0` only after the UIDVALIDITY reset.

## Execution result (2026-06-09)
```
  [PASS] B09_failed_uids_unbounded_until_uidvalidity_reset :: failed_uids_len_after_50_runs=50 (no cap) len_after_uidvalidity_reset=0
```

**Verdict:** ✅ PASS (defect reproduced). `failed_uids` accumulated to 50 with no cap and only cleared on the UIDVALIDITY reset. **Tag: 🆕 NEW, Low.** Practical blast radius is small (a Postgres `bigint[]` tolerates large arrays; poison mail is rare), but it is genuinely unbounded and the whole array is re-serialized on every batch commit, so a pathological mailbox (many permanently-failing messages) bloats the cursor row and slows every sync. Pairs with B08 (the more entries land in `failed_uids` the more permanent the loss). Fix: cap the retained failed set (e.g. keep only entries ≤ the floor's recent window, or evict once the cursor advances well past them) — once the cursor is past a failed UID by a wide margin it never needs to be re-listed. Not tracked in `docs/FIX_BEFORE_PROD.md`.
