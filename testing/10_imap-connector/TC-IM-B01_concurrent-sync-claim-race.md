| ID · Suite · Type · Mode | TC-IM-B01 · B (sync lifecycle) · Concurrency · http |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ✅ Pass | — (positive/contract) | Info | Executed |

## Objective
Two concurrent `POST /connectors/{id}/sync` must yield exactly one `202` (claim won, runner spawned) and one `409` — never two runners, never a `500`. The single-row conditional UPDATE (`claim_for_sync`) is the only concurrency gate.

## Break hypothesis
If the claim were a SELECT-then-UPDATE (a TOCTOU), both requests could read "idle", both claim, both spawn a runner, and two background syncs would double-process the mailbox. The defence is that `claim_for_sync` is a single atomic `UPDATE ... WHERE sync_status != 'running'` whose Postgres row lock serializes the two writers — the second sees `running` and returns 0 rows → `SyncAlreadyRunningError` → 409.

## Steps
1. Seed a connection on a run-stamped throwaway org, host `192.0.2.1` (RFC 5737 TEST-NET-1, unroutable → the spawned runner fails fast, no real IMAP).
2. Forge a `company_admin` token in-container (server's own JWT secret + connector cipher).
3. `asyncio.gather` two concurrent `POST /connectors/{id}/sync`.
4. Assert status codes `== [202, 409]`; assert exactly one `running` ledger row + total ledger rows `== 1` (the loser raises before `start_run`, inserting no row); assert the 409 body is a clean conflict, not a 500.

## Expected
One 202 + one 409; exactly one `connector_sync_run` row; 409 detail "A sync is already running for this connection."

## Execution result (2026-06-09)
```
docker compose exec -T backend python - < testing/10_imap-connector/harness/sync_claim_race_http.py

seeded connection e5d72833-26b0-4e2e-89fa-1080717c7c05 on throwaway org b8a9f0a3-c3e6-426c-b825-8fb30adce714
  [PASS] B01_concurrent_sync_one_202_one_409 :: status_codes=[202, 409] (expected [202, 409])
  [PASS] B01_exactly_one_running_ledger_row :: running_ledger_rows=1 total_ledger_rows=1 (loser inserts none → total must be 1; running may already be finalized)
  [PASS] B01_409_is_clean_conflict_not_500 :: 409_body={"detail":"A sync is already running for this connection."}
cleanup: deleted connector_connection rows for org b8a9f0a3-c3e6-426c-b825-8fb30adce714 (CASCADE)

RESULT: 3/3 checks passed
```

**Verdict:** PASS. The atomic conditional-UPDATE claim held under a real concurrent race against the live server: exactly one runner spawned, the loser got a clean 409 (not a 500), and exactly one ledger row exists. **Tag: — (positive/contract).** The defence held.
