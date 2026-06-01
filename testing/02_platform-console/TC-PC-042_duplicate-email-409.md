# TC-PC-042: Duplicate admin email → 409

| Field | Value |
|---|---|
| **ID** | TC-PC-042 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | ONB — Onboarding contracts + input validation/fuzz |
| **Type** | Adversarial |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Onboarding a NEW org slug whose admin email already belongs to a user is rejected with **409** —
the admin email is globally unique across the platform.

## Break hypothesis
The second onboard succeeds (creating a duplicate user) or returns a 500 from the `users.email`
UNIQUE violation rather than a clean 409.

## Preconditions
- Live stack; demo platform admin token.
- Run-stamped: slug `onb42-<stamp>-a` / `…-b`; shared email `onb42-<stamp>@oneai.dev`.

## Steps
1. Onboard slug A with email E → 201.
2. Onboard NEW slug B reusing email E → 409.

## Expected result
First: 201. Second: 409 with `detail` "A user with this email already exists."

## Harness
Script: `harness/tc_042.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_042.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 08:52 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> First onboard 201; the new-slug-reused-email onboard returns 409.

**Evidence**

```
first onboard (email E): 201 -> {'organization': {'id': '3b0976fa-...', 'slug': 'onb42-19e82623a4993f4-a', ...}, 'admin': {'email': 'onb42-19e82623a4993f4@oneai.dev', ...}}
second onboard (new slug, same email): 409 -> {'detail': 'A user with this email already exists.'}
```

**Verdict**

Defense held. The pre-insert guard `if await self._users.email_exists(payload.admin_email)`
(`platform_auth_service.py:156-157`) raises `DuplicateUserError` → 409 (`error_handlers.py:44`).
NOTE: `email_exists` is unscoped (global) by design — that is the documented cross-tenant
email-existence oracle (FIX_BEFORE_PROD AUD-04). Here it is the intended uniqueness guard and the
409 is correct behaviour. TC-PC-043 proves the abort leaves no orphan org.

**Notes / follow-up**

Cross-references the AUD-04 deferral (global email uniqueness). The atomic-rollback proof is 043.
