# TC-OL-034: PATCH status accepts all four valid values (echoed), finishes active

| Field | Value |
|---|---|
| **ID** | TC-OL-034 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Positive |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`PATCH /platform/orgs/{id}/status` accepts each of the four valid `OrganizationStatus`
values (`active`, `suspended`, `onboarding`, `offboarded`), returns 200, and echoes the new
status in the detail body (PC-03a-AC1). Org is left on `active`.

## Break hypothesis
A valid value is rejected, or the response echoes the old status (stale read), or the write
doesn't take effect (the in-memory body and the persisted row diverge).

## Preconditions
Live stack. Fresh run-stamped org (`contract34-<stamp>`) — safe to mutate. Demo platform
token. HARD RULE: must finish on `active`.

## Steps
1. Platform-login; `provision_company(prefix="contract34")`.
2. PATCH status → active, suspended, onboarding, offboarded, active (in order).
3. Assert each is 200 and echoes the value just set.
4. FRESH GET read-back; assert final status == `active`.

## Expected result
200 with the matching status echoed for each; final read-back `active`.

## Harness
Script: `harness/tc_034.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_034.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> All four valid values were accepted with 200 and the new status echoed each time. The
> independent FRESH GET read-back confirmed the org finished on `active` (write persisted,
> not just an in-memory echo).

**Evidence**

```
PATCH status=active -> 200 echoed_status=active
PATCH status=suspended -> 200 echoed_status=suspended
PATCH status=onboarding -> 200 echoed_status=onboarding
PATCH status=offboarded -> 200 echoed_status=offboarded
PATCH status=active -> 200 echoed_status=active
final read-back status=active (must be 'active')
```

psql ground-truth (post-run): `contract34-… | active | f`

**Verdict**

The defense held. `PlatformOrgService.set_status`
(`backend/app/identity/services/platform_org_service.py:43-56`) sets `organization.status`
and the `get_session` dependency commits; the FRESH GET read-back proves persistence (not a
stale in-memory echo). Confirms PC-03a-AC1.

**Notes / follow-up**

Org finished `active` per HARD RULE; psql verified. Related: TC-OL-036 (legal-hold persist
read-back), the suspend gate (suite probe / ⭐ cases elsewhere).
