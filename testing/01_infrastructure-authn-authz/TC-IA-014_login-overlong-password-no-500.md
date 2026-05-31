<!--
  TC-IA-014 — a >72-byte password at login must be 401, not a 500 (bcrypt ValueError
  swallowed). See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-IA-014: Login with a >72-byte password returns 401, never a 500

| Field | Value |
|---|---|
| **ID** | TC-IA-014 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authentication / login (AUTHN) |
| **Type** | Boundary |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`LoginRequest.password` allows up to 256 chars (plain `Field`, NOT `BcryptPassword`), so a
password longer than bcrypt's 72-byte input limit reaches `verify_password`. Verify the
login path returns a clean 401 (the bcrypt `ValueError` is swallowed → `False`), NOT an
opaque 500.

## Break hypothesis
Attacker bet (DoS / info-leak): a >72-byte password makes `bcrypt.checkpw` raise
`ValueError`; if `verify_password` did NOT catch it, the request would 500 with a stack
trace — an unauthenticated, payload-triggered crash on the core login path. The audit's
AUD-02/AUD-09 covered the *hashing* path (user creation → fixed via `BcryptPassword`);
this case checks the *verify* path on `/auth/login`, which deliberately accepts long input.

## Preconditions
Live stack. Harness onboards a fresh org `authn014-<stamp>` (admin), then logs in with:
(A) the REAL email + a 200-byte password (>72 bytes), and (B) a ghost email + the same
200-byte password. Both must be 401, not 500.

## Steps
1. Onboard org + admin.
2. Build a 200-character ASCII password (= 200 bytes, well over 72).
3. Response A: POST `/auth/login` {real_email, long_pw} → expect 401.
4. Response B: POST `/auth/login` {ghost_email, long_pw} → expect 401.
5. Assert neither is 500 and both bodies are the generic `detail`.

## Expected result
- Both `401` (NOT 500, NOT 422 — `max_length=256` admits 200 chars).
- Both bodies == `{"detail": "Invalid email or password."}`.

## Harness
Script: `harness/tc_014.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_014.py`

---

## Execution result

- **Run at:** 2026-05-31 11:55 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> A 200-byte password passes schema validation (under `max_length=256`) and reaches
> `verify_password`, which swallows the bcrypt `ValueError` and returns False → the login
> yields a clean 401 with the generic message for both the real-email and ghost-email
> cases. No 500, no stack trace.

**Evidence**

```
== onboard == 201 admin=admin-authn014-19e7d327fa9e3d7@oneai.dev
password length: 200 chars / 200 bytes (bcrypt limit = 72)
== A: real email + 200-byte password == 401
   body: {'detail': 'Invalid email or password.'}
== B: ghost email + 200-byte password == 401
   body: {'detail': 'Invalid email or password.'}
any 500 observed: False
RESULT: PASS
```

**Verdict**

Defense held — CONFIRMS-FIXED. `verify_password` (`security/password.py:51-54`) wraps
`bcrypt.checkpw` in `try/except ValueError: return False`, so the over-72-byte input on the
login *verify* path degrades to a normal auth failure (401) instead of the 500 that the
unguarded *hashing* path once produced (AUD-02). The login surface is not a payload-triggered
crash vector. AUD-09's claim that "verify_password correctly swallows it" is confirmed live.

**Notes / follow-up**

The corresponding *hashing*-path fix (BcryptPassword on create/onboard → 422) is exercised
by the User-management suite; this case isolates the login-verify branch.
