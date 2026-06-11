| ID · Suite · Type · Mode | TC-IM-B06 · B (sync lifecycle) · Positive · runner |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ✅ Pass | — (positive/contract) | Info | Executed |

## Objective
Cursor resume across a UIDVALIDITY change must re-scan from UID 1 and reset the tracker floor — the cursor lands on the NEW generation's real high-water, never advancing past the stale pre-reset floor. (Reconfirm of `test_run_uidvalidity_reset_advances_to_the_real_high_water_not_the_old_floor`.)

## Break hypothesis
If the runner kept the old high-water floor (1000) after a UIDVALIDITY reset, `highest_contiguous_uid` would advance the cursor past mail that was never fetched in the new generation → permanent silent loss. The defence: `_ingest_batch` (`connector_sync_runner.py:254-256`) drops the tracker and starts fresh at floor 0 when `tracker.uidvalidity != batch.uidvalidity`.

## Steps (prove once)
1. Seed a cursor at `uidvalidity=111, last_seen_uid=1000`. Claim; run a batch under NEW `uidvalidity=222` carrying UIDs 1,2,3.
2. Assert the stored cursor is `(222, 3)` — the new generation's high-water, not 1000.

## Expected
`uidvalidity=222, last_seen_uid=3`.

## Execution result (2026-06-09)
```
  [PASS] B06_uidvalidity_reset_resets_floor :: uidvalidity=222 last_seen_uid=3 (bug would leave last_seen_uid=1000)
```

**Verdict:** ✅ PASS. The tracker floor reset to 0 on the generation change and the cursor advanced to the real high-water (3), not the stale 1000. **Tag: — (positive/contract).** Reconfirmed live.
