# TC-BG-013: `GET /platform/support-requests` is requester-scoped

| Field | Value |
|---|---|
| **ID** | TC-BG-013 · **Suite** ISO · **Type** Negative/Adversarial (requester scope) · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
request as forged identity F → 201 | F's list 200 present=True (positive control)
demo admin's list 200 absent=True (demo_count=76, asserted by grant_id not count)
```
**Verdict:** Defense held. The platform list is requester-scoped (`list_for_requester` →
`support_grant_repository.py:74-81` `WHERE requested_by_admin_id = :me`): another identity's grant is present
in its own list but absent from the demo admin's. No admin sees every admin's requests. PC-05-AC5 + review #2.
