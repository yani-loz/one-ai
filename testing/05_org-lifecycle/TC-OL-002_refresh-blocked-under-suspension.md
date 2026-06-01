<!--
  SUSPEND suite — suspend-blocks-login gate (the ⭐ security core).
-->

# TC-OL-002: Pre-suspension refresh token is blocked under suspension (403)

| Field | Value |
|---|---|
| **ID** | TC-OL-002 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | SUSPEND — suspend-blocks-login gate ⭐ |
| **Type** | Negative (security) |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-03a-AC3 on the refresh surface: a long-lived refresh token minted **before** suspension
cannot extend the session **after** the org is suspended — `/auth/refresh` returns 403. A suspension
must not be outlivable by a pre-issued refresh token.

## Break hypothesis
If the suspend gate only guarded `/auth/login` and not `/auth/refresh`, a suspended org's user could
keep minting fresh access tokens indefinitely by rotating a pre-suspension refresh token — defeating
the suspension entirely. The defense is the second `_load_loginable_org` call inside `refresh()`
(`auth_service.py:103`), after the token is consumed and the user resolved.

## Preconditions
- Live stack up. Fresh run-stamped org via `provision_company(c, plat, "sus002")`; its admin's
  **pre-suspension** refresh token captured. Throwaway — safe to suspend.

## Steps
1. Provision a fresh company; capture the admin's pre-suspension refresh token.
2. Suspend the org (`PATCH .../status` → `suspended`).
3. `POST /auth/refresh` with the pre-suspension refresh token → expect 403.
4. Reactivate (cleanup).

## Expected result
- `403 {"detail":"Your organization's access is suspended."}` — the refresh is blocked exactly like
  login, so a pre-issued refresh token cannot outlive the suspension.

## Harness
Script: `harness/tc_002.py` · run: `cat testing/05_org-lifecycle/harness/_common.py testing/05_org-lifecycle/harness/tc_002.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Under suspension, presenting the pre-suspension refresh token to `/auth/refresh` returned
> `403 {"detail":"Your organization's access is suspended."}`. The suspend gate is enforced on the
> refresh path, not only on login.

**Evidence**

```
== TC-OL-002 — pre-suspension refresh token blocked under suspension (AC3) ==
[setup]   org f3884b24-138f-4983-a113-629203eff73c pre-suspension refresh captured
[suspend]  PATCH status=suspended: 200 status=suspended
[refresh]  /auth/refresh pre-suspension token: 403 body=b'{"detail":"Your organization\'s access is suspended."}'
RESULT: PASS — suspended org cannot extend its session; refresh blocked (403)
```

**Verdict**

The defense held. `AuthService.refresh` consumes the token, resolves the user, then calls
`await self._load_loginable_org(user.org_id)` (`backend/app/identity/services/auth_service.py:103`),
which raises `OrganizationSuspendedError` → 403 (`error_handlers.py:43`) when the org is suspended. A
long-lived refresh token cannot outlive a suspension. CONFIRMS-FIXED (PC-03a-AC3, refresh surface).

**Notes / follow-up**
The deeper question — whether this 403 *consumes/burns* the refresh token — is TC-OL-003 (the
rollback case). This case only proves the gate fires on refresh; TC-OL-003 proves it does so without
destroying the session.
