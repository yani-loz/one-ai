# TC-BG-014: `POST /platform/support-requests/{id}/revoke` is requester-scoped (404 for another's)

| Field | Value |
|---|---|
| **ID** | TC-BG-014 · **Suite** ISO · **Type** Negative/Adversarial (requester scope) · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
request as forged identity F → 201 | demo admin revoke F's grant → 404 | F revoke own → 200 revoked
psql audit: requested + revoked both actor_id=F (demo admin's 404'd attempt left ZERO trace)
```
**Verdict:** Defense held. `get_for_requester(grant, me)` → None → 404 (`platform_support_service.py:96-98`;
repo `:43-59` `WHERE id AND requested_by_admin_id FOR UPDATE`). Only the requester can revoke their own grant;
the rejected attempt is not even logged. PC-05-AC5 (`test_platform_cannot_revoke_another_admins_grant`).
