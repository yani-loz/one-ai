# TC-BG-035: SQL-injection reason stored literally — parameterized, content-blind, table intact

| Field | Value |
|---|---|
| **ID** | TC-BG-035 · **Suite** AEA · **Type** Adversarial/Fuzz · **Severity if fail** Critical |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
reason = "Robert'); DROP TABLE support_grant;--" → POST 201, returned reason == input (round-tripped)
psql: to_regclass('public.support_grant') = support_grant (table intact); stored reason = literal payload
follow-up request still succeeds (table usable)
```
**Verdict:** Defense held. The ORM insert (`support_grant_repository.py:37-41`) parameterizes the value so
the DROP is inert text; the response carries only `SupportGrantResponse` metadata keys (content-blind). The
DROP never executed.
