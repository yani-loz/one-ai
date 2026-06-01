# TC-OL-043: The auth gate is present on every new route (missing-bearer 401 ×3)

| Field | Value |
|---|---|
| **ID** | TC-OL-043 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Negative / Authz (coverage) |
| **Severity if it fails** | High (a single ungated lifecycle route = unauthenticated org mutation) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Confirm the missing-bearer 401 of TC-OL-040 holds on EACH of the three new routes
independently — the `get_current_platform_admin` gate is wired on GET detail AND PATCH status
AND PATCH legal-hold, not just one of them (PC-03a-AC6 family — every new route is gated).

## Break hypothesis
One of the three new routes omits the platform-admin dependency, so an unauthenticated caller
can read or mutate an org through that single hole.

## Preconditions
Live stack. Fresh run-stamped org (`contract43-<stamp>`) for a real path id. Valid bodies on
the PATCHes (so any 401 is purely the gate). No Authorization header.

## Steps
1. `provision_company(prefix="contract43")`.
2. With no Authorization: `GET /platform/orgs/{id}`, `PATCH …/status {status:"active"}`,
   `PATCH …/legal-hold {legal_hold:false}`.
3. Assert all three are 401.

## Expected result
401 on each of the three routes; `all_three_401 == True`.

## Harness
Script: `harness/tc_043.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_043.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> All three routes returned 401 with no Authorization header — the gate is present on every
> new lifecycle route, not just the read.

**Evidence**

```
GET detail (no bearer)       -> 401 (expect 401)
PATCH status (no bearer)     -> 401 (expect 401)
PATCH legal-hold (no bearer) -> 401 (expect 401)
all_three_401=True
```

**Verdict**

The defense held. All three routes declare
`_admin: Principal = Depends(get_current_platform_admin)` in
`routes/platform_routes.py:133-162`, so the platform-admin gate runs before any handler
logic on each. No route is ungated. Confirms the PC-03a-AC6 "all three new endpoints" gating
coverage.

**Notes / follow-up**

Org untouched. This is the coverage complement to TC-OL-040 (same probe, emphasis on per-route
completeness). TC-OL-041/042 cover invalid-token (vs missing-token) rejection.
