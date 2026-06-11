| ID · Suite · Type · Mode | TC-IM-B03 · B (sync lifecycle) · Adversarial (TOCTOU) · runner/service |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ✅ Pass (defect reproduced) | 🆕 NEW | Low | Executed |

## Objective
A TOCTOU between `start_sync`'s `disabled_at` check and the claim makes the user-facing error WRONG: the caller is told `409 "already running"` when the truth is the connection was just disabled (which should be `409 "disabled — enable it before syncing"`).

## Break hypothesis
`SyncService.start_sync` checks `connection.disabled_at is not None` (`sync_service.py:75`) and only then calls `claim_for_sync` (`sync_service.py:79`). The claim's WHERE includes `disabled_at.is_(None)` (`connector_connection_repository.py:96`). If an admin disables the connection in the window between :75 and :79, the service's up-front guard already passed (it saw the row active), but the claim's `disabled_at IS NULL` predicate now fails → 0 rows → `claimed=False` → `SyncAlreadyRunningError` (`sync_service.py:82-83`). The user gets the wrong reason for the 409.

## Steps
1. Seed an active (NOT disabled) connection so the service's `:75` guard passes.
2. Drive the service with a `ConnectorConnectionRepository` subclass whose `claim_for_sync` first sets `disabled_at=now()` (same uncommitted txn, visible to the next statement) then calls `super().claim_for_sync(...)` — landing the disable precisely in the :75→:79 window.
3. Call `start_sync`; record which exception type is raised.

## Expected
`SyncAlreadyRunningError` is raised (the defect) instead of `ConnectorDisabledError` (the truthful error).

## Execution result (2026-06-09)
```
  [PASS] B03_toctou_disable_returns_misleading_already_running :: raised=SyncAlreadyRunningError (expected ConnectorDisabledError; SyncAlreadyRunningError = the defect)
```

**Verdict:** ✅ PASS (defect reproduced). The race produces a misleading 409: the API tells the company-admin "a sync is already running" when in fact the connection was disabled mid-request. **Tag: 🆕 NEW, Low.** No data is exposed or lost and isolation holds — the only harm is an operator-confusing error message in a narrow race window. The claim docstring's "no SELECT-then-UPDATE TOCTOU" claim is true for *isolation* (the claim is atomic) but the *disabled-reason* surfacing is non-atomic with the claim. Fix: on `claimed=False`, re-read the row and disambiguate disabled-vs-running before choosing the exception. Not tracked in `docs/FIX_BEFORE_PROD.md`.
