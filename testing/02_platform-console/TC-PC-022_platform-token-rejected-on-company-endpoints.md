<!--
  XDOM suite — cross-domain confinement. See ../README.md for legend/tags.
-->

# TC-PC-022: Real PLATFORM token rejected on COMPANY endpoints (`/auth/me`, `/users`)

| Field | Value |
|---|---|
| **ID** | TC-PC-022 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Confine the OTHER direction: a REAL, valid platform access token (aud='platform') must be
rejected (401) on the company surface — `GET /auth/me` and `GET /users`. Combined with
TC-PC-020/021 this proves both domains are mutually sealed.

## Break hypothesis
If `get_current_principal` did not pin the audience to `company`, a valid platform token would
decode and build a Principal; `/auth/me` would then attempt to resolve a user by the platform
admin's id. (Note: the 401 here has a secondary cause too — the platform sub is absent from the
`users` table, and on `/users` a removed audience check would surface a 403 via the role gate —
so this case proves *confinement holds*, not that the audience guard is the sole load-bearing
element the way TC-PC-020/026 do.)

## Preconditions
- Live stack up; real platform access token from `platform_login_pair`.

## Steps
1. `platform_login_pair` → real platform access token.
2. `GET /auth/me` with that token.
3. `GET /users` with that token.

## Expected result
- Both → `401` (never 403, never 200, never 500). Audience mismatch rejects in
  `get_current_principal` before any user lookup or role check.

## Harness
Script: `harness/tc_022.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_022.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The real platform access token was rejected with 401 on both company endpoints, with the
> invalid-token (audience) detail. The platform domain cannot reach the company surface.

**Evidence**

```
== TC-PC-022 — real PLATFORM token rejected on COMPANY endpoints (/auth/me, /users) ==
[attack1] GET /auth/me (platform access token): 401
          body: {'detail': 'Access token is invalid.'}
[attack2] GET /users (platform access token): 401
          body: {'detail': 'Access token is invalid.'}
RESULT: PASS — both directions confined (platform token cannot reach company endpoints)
```

**Verdict**

Confinement holds. `get_current_principal` (`backend/app/identity/dependencies.py:86`) calls
`decode_access_token(..., COMPANY_AUDIENCE)`, so a platform-aud token fails the audience check
and 401s before the user lookup (`/auth/me`) or the `require_company_admin` role gate
(`/users`). CONFIRMS-FIXED of the FIX_BEFORE_PROD invariant "Keep platform-admin auth
physically separate" (both directions 401, not silently accepted). Phrased as confinement —
unlike TC-PC-020/026, the audience guard is not the *sole* reason for these particular 401s
(secondary not-found / role-gate paths exist), so this case is corroboration, not a
discriminating proof.

**Notes / follow-up**
Pairs with TC-PC-023 (company token rejected on platform endpoints). Together they show the
full mutual seal between the two auth domains.
