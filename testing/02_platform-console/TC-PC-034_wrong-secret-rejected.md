<!-- PAZ suite — Platform token-validation matrix (401 not 403/500). -->

# TC-PC-034: Platform token signed with the wrong secret → 401

| Field | Value |
|---|---|
| **ID** | TC-PC-034 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PAZ — Platform token-validation matrix |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove a structurally-perfect token (correct `aud='platform'`, real demo admin sub, valid HS256
structure) signed with a **different secret** is rejected — the JWT secret is the single isolation
layer (RLS inert), so this is the load-bearing control.

## Break hypothesis
If the server verified with the wrong key, accepted unverified, or had a key-confusion flaw, a
token signed with any secret would authenticate. The bet: a wrong-secret token returns 200.

## Preconditions
- Live stack up. Token forged via `forge_platform_token(sub=<real demo admin>,
  secret='not-the-real-secret')`. Demo admin untouched (read-only `/me`).

## Steps
1. Forge a platform token signed with `'not-the-real-secret'`.
2. `GET /platform/me`. Record status + body.

## Expected result
- HTTP **401**, body `{"detail": "Access token is invalid."}` (HMAC mismatch against the server's
  real `jwt_secret`).

## Harness
Script: `harness/tc_034.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_034.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> A token signed with a foreign secret was rejected with **401**. (PyJWT emitted a client-side
> `InsecureKeyLengthWarning` while *forging* the 19-byte test secret — that is the harness minting
> the hostile token, not the server; the server's verdict is the 401.)

**Evidence**

```
/opt/venv/.../jwt/api_jwt.py:147: InsecureKeyLengthWarning: The HMAC key is 19 bytes long ...
WRONG-SECRET /platform/me -> 401 {"detail":"Access token is invalid."}
assert_401: PASS (got 401)
```

**Verdict**
Defense held — this is the core of the "JWT secret is the only active isolation layer" model.
Code path: `decode_access_token` (`backend/app/identity/security/tokens.py:77-88`) verifies the
HMAC against `settings.jwt_secret`; a foreign-key signature raises `InvalidSignatureError ⊂
InvalidTokenError` → `TokenInvalidError` → 401. Confirms forging requires the real dev secret
(`FIX_BEFORE_PROD.md`: rotate `JWT_SECRET` before prod — the dev default is the forgeable lever).

**Notes / follow-up**
Together with TC-PC-031 (alg=none) and TC-PC-032 (tampered sig), this closes the
"mint/mutate without the secret" attack class for the platform domain.
