| ID · Suite · Type · Mode | TC-IM-B10 · B (sync lifecycle) · Adversarial · runner |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ✅ Pass (defect reproduced) | 🆕 NEW | Low | Executed |

## Objective
Probe fence integrity under a corrupted fencing token: with `sync_status='running'` but `sync_run_id` set to NULL, every fenced write by the live run misses, AND a fresh claim is blocked while the heartbeat is still fresh — the connection is wedged in an inconsistent `running` + `sync_run_id IS NULL` state until the stale window elapses.

## Break hypothesis
`_fenced_update` (`connector_connection_repository.py:150-163`) gates every heartbeat/progress/finalize on `sync_run_id == run_id`. If `sync_run_id` is corrupted to NULL (a bad migration, a manual fix, a partial write), `NULL == run_id` is never true → the live run's writes all return 0 rows and it can neither make progress nor finalize. Meanwhile `claim_for_sync` (`:97-101`) is claimable only if `sync_status != 'running'` OR the heartbeat is stale — so while the heartbeat is fresh, a new claimant is ALSO blocked. The connection is stuck "running forever, owned by nobody" until `sync_heartbeat_at` ages past `STALE_SECONDS=300`, at which point a reclaim self-heals it.

## Steps
1. Seed; claim with run A; confirm run A owns the heartbeat fence (returns True).
2. Corrupt: `UPDATE ... SET sync_run_id = NULL` (leaving `sync_status='running'`).
3. Assert run A's fenced heartbeat now returns False (write missed).
4. Assert a fresh `claim_for_sync` is BLOCKED while the heartbeat is fresh.
5. Age the heartbeat 10 min past the window; assert a reclaim now SUCCEEDS (self-heals).

## Expected
`owned_before=True`, `fenced_after=False`, fresh claim blocked, stale reclaim succeeds.

## Execution result (2026-06-09)
```
  [PASS] B10_corrupt_run_id_wedges_until_stale_window :: owned_before=True fenced_write_after_corrupt=False fresh_claim_blocked_while_fresh=True reclaim_after_stale=True
```

**Verdict:** ✅ PASS (defect reproduced, bounded). A NULL `sync_run_id` with `sync_status='running'` wedges the connection: the owning run can't write or finalize, and no new run can claim it — until the 300 s stale window elapses, after which a reclaim recovers it. **Tag: 🆕 NEW, Low.** This is a fail-SAFE inconsistency, not a fail-open one: no isolation breach, no data loss, and it auto-recovers within `STALE_SECONDS`; reaching it requires an out-of-band write that corrupts the fencing token (the application code never sets `sync_run_id=NULL` while `sync_status='running'`). The residual risk is purely a self-healing ≤5 min stall plus a `running`+`NULL` state the status UI would render as "syncing" with no live runner. The stale-reclaim window is exactly the designed crash-recovery mechanism doing its job. Not tracked in `docs/FIX_BEFORE_PROD.md`.
