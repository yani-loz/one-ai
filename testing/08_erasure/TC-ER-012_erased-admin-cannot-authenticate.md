# TC-ER-012: An erased org's admin can no longer authenticate (AC2)

| Field | Value |
|---|---|
| **ID** | TC-ER-012 · **Target** Erasure (PC-06) · **Suite** ERASE |
| **Type** | Negative · **Severity if fail** High · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-FIXED |

## Objective
Erasure is a real access cutoff: the erased org's admin can authenticate before, but not after.

## Steps / Harness
Provision E3; login (200); `erase_org(E3)` (200); login again. `harness/tc_012.py`.

## Execution result
- **Run at:** 2026-06-01 · **Result:** ✅ Pass · **Tag:** CONFIRMS-FIXED

**Evidence**
```
PRE-ERASE login 200 | ERASE 200 users_erased=1 | POST-ERASE login 401 {"detail":"Invalid email or password."}
```

**Verdict**
Defense held. The user row is deleted (`delete_all_in_org`, `erasure_service.py:117`), so the login lookup
misses and no token issues — a real re-auth cutoff, with the generic 401 (no enumeration leak). PC-06-AC2 confirmed.
