# TC-BG-011: B-admin approving A's grant → 404, untouched (no existence oracle)

| Field | Value |
|---|---|
| **ID** | TC-BG-011 · **Suite** ISO · **Type** Negative/Adversarial · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
B-admin approve A's grant → 404 {"detail":"Support grant not found."}
B-admin approve NONEXISTENT grant_id → 404 IDENTICAL body (oracle-safe) | psql: grant still requested|NULL|NULL
```
**Verdict:** Defense held. `get_in_org(grant, caller_org)` → None → 404 (`company_support_service.py:131-133`),
byte-identical to a truly-nonexistent id, grant untouched. Not 403/200-empty — no existence leak, no
cross-tenant consent forgery. PC-05-AC3.
