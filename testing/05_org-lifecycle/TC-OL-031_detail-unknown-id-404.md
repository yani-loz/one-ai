# TC-OL-031: Detail for an unknown org id → 404

| Field | Value |
|---|---|
| **ID** | TC-OL-031 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Negative |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`GET /platform/orgs/{id}` for a well-formed but nonexistent org id returns 404 with the
generic `OrganizationNotFoundError` body — not 200/empty, not 500 (PC-03a-AC2).

## Break hypothesis
The service returns 200 with a null/empty body for a missing org (leaking existence
semantics or breaking the contract), or it raises an unhandled error → 500.

## Preconditions
Live stack. Random `uuid4()` that belongs to no org. Demo platform token.

## Steps
1. Platform-login.
2. `GET /platform/orgs/<random uuid4>`.
3. Assert 404.

## Expected result
404 with `{"detail": "Organization not found."}`.

## Harness
Script: `harness/tc_031.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_031.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> 404 with the generic not-found detail.

**Evidence**

```
GET /platform/orgs/9e38387b-c222-44bb-be25-096bde21cb48
status=404 (expect 404)
body={"detail":"Organization not found."}
```

**Verdict**

The defense held. `PlatformOrgService._load`
(`backend/app/identity/services/platform_org_service.py:70-75`) raises
`OrganizationNotFoundError` when the repo returns `None`, mapped to 404 in
`error_handlers.py:44`. Confirms PC-03a-AC2.

**Notes / follow-up**

No org was created by this case (random id). Pairs with TC-OL-035 / TC-OL-038 (PATCH on
unknown id → 404).
