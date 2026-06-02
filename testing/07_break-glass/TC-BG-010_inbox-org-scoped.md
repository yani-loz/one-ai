# TC-BG-010: Support inbox is org-scoped — A's grant visible to A, absent from B

| Field | Value |
|---|---|
| **ID** | TC-BG-010 · **Suite** ISO · **Type** Negative/Adversarial (cross-tenant) · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
request 201 grant_id=0b0cefe7... org_id=A | A inbox 200 grant_in_A=True | B inbox 200 grant_in_B=False
```
**Verdict:** Defense held. The HITL inbox is scoped by the verified JWT `org_id`
(`company_support_service.list_for_org` → `support_grant_repository.py:83-90` `WHERE org_id`). A grant
targeting A never appears in B's inbox — no cross-tenant existence/metadata leak. PC-05-AC3 (inbox).
