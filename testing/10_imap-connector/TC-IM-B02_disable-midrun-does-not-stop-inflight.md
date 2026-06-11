| ID · Suite · Type · Mode | TC-IM-B02 · B (sync lifecycle) · Adversarial · runner |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ⚠️ Pass-with-concern | 🆕 NEW | Low | Executed |

## Objective
Confirm that disabling a connection mid-run does NOT stop the in-flight sync: `disable` only sets `disabled_at` (which gates FUTURE claims), and the running batch keeps ingesting into a connection the admin just disabled.

## Break hypothesis
`disable` sets `disabled_at` (`connector_connection_repository.py:96` — the claim's `disabled_at IS NULL` predicate gates new claims). But the runner's `_sync` (`connector_sync_runner.py:158-233`) loads the connection once via `get_in_org` and NEVER re-reads `disabled_at` on any subsequent batch. So an admin who disables an active connection to "stop the sync now" does not stop it — the run finishes, ingests mail, and stamps `last_synced_at`, all against a disabled connection.

## Steps
1. Seed an active connection; claim it; open the ledger; commit.
2. Admin disables it AFTER the claim is committed (the real sequence: claim → admin disables → runner proceeds).
3. Run the runner over a one-message batch.
4. Measure: emails stored, `sync_status`, `last_synced_at`, `disabled_at`.

## Expected
The run completes despite `disabled_at` being set: 1 email stored, `sync_status='idle'`, `last_synced_at` advanced — proving disable is a future-claim gate, not an in-flight kill switch.

## Execution result (2026-06-09)
```
  [PASS] B02_disable_midrun_does_not_stop_inflight :: stored=1 sync_status=idle last_synced_set=True disabled_at_set=True
```

**Verdict:** ⚠️ Pass-with-concern — the hypothesis reproduced exactly: an in-flight run ingested into a connection the admin had disabled, advanced `last_synced_at`, and finalized clean. **Tag: 🆕 NEW, Low.** This is a design-gap observation, not corruption: a bounded already-started run completing is arguably acceptable, but "Disable" in the UI implies "stop now," and the runner honours no such signal — there is no cooperative cancellation on `disabled_at`. A `disable` that wants to be immediate would need the runner to re-check `disabled_at` per batch (and abort like a stolen claim). Not tracked in `docs/FIX_BEFORE_PROD.md`.
