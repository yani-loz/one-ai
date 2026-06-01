<!-- PAZ suite — Platform token-validation matrix (401 not 403/500). -->

# TC-PC-033: Expired platform token on `GET /platform/me` → 401 (TokenExpiredError path)

| Field | Value |
|---|---|
| **ID** | TC-PC-033 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PAZ — Platform token-validation matrix |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove an **expired** but otherwise-valid (correct secret + `aud='platform'` + real demo admin sub)
token is rejected with 401 — exercising the `TokenExpiredError` branch, which must be checked
before generic invalidity (PyJWT's `ExpiredSignatureError ⊂ InvalidTokenError`).

## Break hypothesis
If `exp` were not enforced (e.g. `verify_exp` disabled, or the `require:["exp"]` option absent
combined with a missing/ignored claim), a token would outlive its TTL and a stolen 15-min token
would work forever. The bet: an expired token still authenticates (200).

## Preconditions
- Live stack up. Token forged with `forge_platform_token(sub=<real demo admin>, expired=True)` —
  `iat`/`exp` both in the past, valid signature, correct audience. Demo admin untouched (read-only).

## Steps
1. Forge an expired platform token with the real demo admin sub.
2. `GET /platform/me`. Record status + body; print the past `exp`/`iat` epochs as proof of expiry.

## Expected result
- HTTP **401**, body `{"detail": "Access token has expired."}` — the dedicated expiry message
  (distinct from the generic invalid message), proving the `ExpiredSignatureError` branch ran.

## Harness
Script: `harness/tc_033.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_033.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The token's `iat`/`exp` were both in the past; the server rejected it with **401** and the
> **expiry-specific** detail `Access token has expired.` — confirming `exp` is enforced and the
> expired branch is taken (not the generic invalid path).

**Evidence**

```
forged exp(epoch)=1780303661 iat(epoch)=1780302761 (both in the past)
EXPIRED /platform/me -> 401 {"detail":"Access token has expired."}
assert_401: PASS (got 401)
```

**Verdict**
Defense held. Code path: `decode_access_token` (`backend/app/identity/security/tokens.py:84-86`)
catches `jwt.ExpiredSignatureError` **before** the generic `InvalidTokenError` handler and raises
`TokenExpiredError` → 401 (`error_handlers.py:39`). The distinct message proves the ordering
documented in the file's Key-invariants ("Expiry is checked BEFORE generic invalidity") is real.

**Notes / follow-up**
A stateless access token still works for its full TTL (no denylist) — that is the documented
`FIX_BEFORE_PROD.md` "access-token denylist for immediate revocation" deferral, out of scope here;
this case only asserts the TTL boundary itself is enforced.
