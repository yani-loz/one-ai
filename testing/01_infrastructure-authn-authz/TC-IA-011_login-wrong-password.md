<!--
  TC-IA-011 — correct email, wrong password. Authored top-half first; Execution
  result written back after running the harness against the live stack.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-IA-011: Login with correct email but wrong password returns generic 401

| Field | Value |
|---|---|
| **ID** | TC-IA-011 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authentication / login (AUTHN) |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Verify a real account with a wrong password is rejected with HTTP 401 and the generic
body `{"detail": "Invalid email or password."}` — no field-level hint that the email was
valid but the password wrong.

## Break hypothesis
Violation = any non-401 status (e.g. 200, 403, 500), or a body that distinguishes
"wrong password" from "no such user" (e.g. "password incorrect", "account locked", or a
different shape) — which would create a partial enumeration oracle.

## Preconditions
Live stack. Harness onboards a fresh run-stamped org `authn011-<stamp>` with admin
`admin-authn011-<stamp>@oneai.dev` (password `DEFAULT_PW`), then attempts login with the
correct email and a deliberately wrong password.

## Steps
1. Platform-login, onboard fresh org + admin.
2. Sanity: login with correct creds → expect 200 (account is real).
3. POST `/auth/login` with the correct email and password `"WRONG-Pass-9999!"`.
4. Inspect status + exact body.

## Expected result
- Step 3 status `401`.
- Body == `{"detail": "Invalid email or password."}` (exact string, single `detail` key).

## Harness
Script: `harness/tc_011.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_011.py`

---

## Execution result

- **Run at:** 2026-05-31 11:46 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> A valid account with the wrong password is rejected with 401 and exactly
> `{"detail": "Invalid email or password."}`. The body carries no hint that the email
> existed — identical to the unknown-email case (see TC-IA-012). The generic-message
> contract holds.

**Evidence**

```
== onboard == 201 admin=admin-authn011-19e7d326e81ca0d@oneai.dev
== sanity login (correct creds) == 200 (account is real)
== POST /auth/login (correct email, WRONG password) == 401
body: {'detail': 'Invalid email or password.'}
body keys: ['detail']
detail == 'Invalid email or password.': True
RESULT: PASS
```

**Verdict**

Defense held. `auth_service.login` (`auth_service.py:67-68`) raises
`InvalidCredentialsError("Invalid email or password.")` for the wrong-password branch,
mapped to 401 by `error_handlers.py:37`. No enumeration distinction between
wrong-password and unknown-email.

**Notes / follow-up**

Paired with TC-IA-012, which proves the unknown-email response is byte-identical.
