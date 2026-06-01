# TC-OL-038: PATCH legal-hold on an unknown org id → 404

| Field | Value |
|---|---|
| **ID** | TC-OL-038 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Negative |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`PATCH /platform/orgs/{id}/legal-hold` for a well-formed but nonexistent org id → 404, the
same not-found guard as the read and status-PATCH paths.

## Break hypothesis
Legal-hold PATCH no-ops / create-on-writes on a missing org (200) or 500s.

## Preconditions
Live stack. Random `uuid4()`, valid body `{legal_hold:true}`. Demo platform token.

## Steps
1. Platform-login.
2. `PATCH /platform/orgs/<random uuid4>/legal-hold` with `{legal_hold:true}`.
3. Assert 404.

## Expected result
404 `{"detail":"Organization not found."}`.

## Harness
Script: `harness/tc_038.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_038.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> 404 with the generic not-found body.

**Evidence**

```
PATCH /platform/orgs/a9f6d982-e484-43fe-af71-a8930afe01d3/legal-hold legal_hold=true
-> 404 (expect 404) body={"detail":"Organization not found."}
```

**Verdict**

The defense held. `set_legal_hold` → `_load`
(`backend/app/identity/services/platform_org_service.py:58-75`) raises
`OrganizationNotFoundError` (→ 404) before any mutation. Confirms the legal-hold mutation
path shares the not-found guard.

**Notes / follow-up**

No org created/mutated. Pairs with TC-OL-031 / TC-OL-035.
