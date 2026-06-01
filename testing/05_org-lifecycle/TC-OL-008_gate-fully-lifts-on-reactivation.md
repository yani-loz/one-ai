# TC-OL-008: The gate fully lifts on reactivation (login + refresh both restored)

| Field | Value |
|---|---|
| **ID** | TC-OL-008 · **Target** Org Lifecycle (PC-03a) · **Suite** SUSPEND |
| **Type** | Positive (reversibility) · **Severity if fail** Medium · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-FIXED |

## Objective
Reactivation restores *both* surfaces the suspension blocked — login and refresh — not just login.

## Steps / Harness
`provision_company("sus008")` → suspend → refresh (pre-susp token) 403 → reactivate → fresh login → its
refresh rotates. `harness/_finish_suspend.py` (case 008).

## Execution result
- **Run at:** 2026-06-01 local · **Result:** ✅ Pass · **Tag:** CONFIRMS-FIXED

**Evidence**
```
[008] suspend->refresh=403 (403); reactivate-> fresh-login refresh rotates=200 (200)
```

**Verdict**
Defense held. After reactivation the org passes `_load_loginable_org` again, so a fresh login succeeds and
its refresh token rotates (200). The suspension is fully reversible. Complements TC-OL-003 (the
pre-suspension refresh token *also* survives, since the suspension-403 rolled back its staged revoke).
