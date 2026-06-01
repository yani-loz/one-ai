# TC-OL-036: Legal hold set + persist (fresh GET read-back)

| Field | Value |
|---|---|
| **ID** | TC-OL-036 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Positive |
| **Severity if it fails** | Medium (a non-persisting legal hold gives false compliance assurance) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`PATCH /platform/orgs/{id}/legal-hold` with `true` returns 200 `legal_hold=true` AND a fresh
independent GET reads it back as `true`; setting `false` reads back `false` (PC-03a-AC7,
audit test-1 "added read-back GET").

## Break hypothesis
The PATCH echoes the new value in-memory but the change is not committed, so a fresh GET
(new request/session) still reads the old value — the audit's exact test-1 concern.

## Preconditions
Live stack. Fresh run-stamped org (`contract36-<stamp>`). Demo platform token.

## Steps
1. Platform-login; `provision_company(prefix="contract36")`.
2. PATCH legal-hold `true`; assert 200 + `legal_hold=true`.
3. FRESH GET detail; assert `legal_hold=true`.
4. PATCH legal-hold `false`; FRESH GET; assert `legal_hold=false`.

## Expected result
200 on each PATCH; the FRESH GET reflects the value just written, in both directions.

## Harness
Script: `harness/tc_036.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_036.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> PATCH true → 200 legal_hold=True; the independent fresh GET read it back True. PATCH false
> → 200 legal_hold=False; fresh GET read back False. The change is committed, not just echoed.

**Evidence**

```
PATCH legal_hold=true -> 200 legal_hold=True
FRESH GET read-back -> 200 legal_hold=True (expect true)
PATCH legal_hold=false -> 200 legal_hold=False
FRESH GET read-back -> 200 legal_hold=False (expect false)
```

**Verdict**

The defense held. `PlatformOrgService.set_legal_hold`
(`backend/app/identity/services/platform_org_service.py:58-68`) sets `organization.legal_hold`
and the `get_session` dependency commits; the fresh GET in a new request proves persistence.
Confirms PC-03a-AC7 and the audit's test-1 read-back fix.

**Notes / follow-up**

Org left `legal_hold=false`. Related: TC-OL-039 (legal hold is auth-inert today).
