# TC-OL-035: PATCH status on an unknown org id → 404

| Field | Value |
|---|---|
| **ID** | TC-OL-035 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Negative |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`PATCH /platform/orgs/{id}/status` for a well-formed but nonexistent org id returns 404 —
the mutation path is guarded by the same not-found check as the read path.

## Break hypothesis
The status PATCH creates-on-write or silently no-ops on a missing org (returning 200), or
raises an unhandled error → 500.

## Preconditions
Live stack. Random `uuid4()` with a valid body (`status=suspended`). Demo platform token.

## Steps
1. Platform-login.
2. `PATCH /platform/orgs/<random uuid4>/status` with `{status:"suspended"}`.
3. Assert 404.

## Expected result
404 `{"detail":"Organization not found."}`.

## Harness
Script: `harness/tc_035.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_035.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> 404 with the generic not-found body; no create-on-write, no 500.

**Evidence**

```
PATCH /platform/orgs/1032617f-d166-47e7-afb0-7f3c70291847/status status=suspended
-> 404 (expect 404) body={"detail":"Organization not found."}
```

**Verdict**

The defense held. `set_status` → `_load`
(`backend/app/identity/services/platform_org_service.py:43-75`) raises
`OrganizationNotFoundError` (→ 404) before any mutation. Confirms the mutation path shares
the read path's not-found guard.

**Notes / follow-up**

No org created/mutated (random id, body valid but never reached the write). Pairs with
TC-OL-031 / TC-OL-038.
