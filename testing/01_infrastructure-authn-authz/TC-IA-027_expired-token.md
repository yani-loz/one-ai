# TC-IA-027: Expired token → 401

| Field | Value |
|---|---|
| **ID** | TC-IA-027 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify expiry is enforced: a correctly-signed company token whose `exp` is in the past is
rejected with 401, so the 15-minute access-token lifetime is real and stale tokens cannot
be replayed indefinitely.

## Break hypothesis
The attacker's bet: `exp` is not validated (e.g. `verify_exp` disabled), so a captured
token works forever — turning any single token theft into permanent access. A 200 on an
expired-but-validly-signed token is the defect.

## Preconditions
- Live stack. Token forged with `forge_company_token(expired=True)` — signed with the real
  dev secret (so signature/aud pass) but `iat`/`exp` set in the past.

## Steps
1. Forge an expired company token (valid signature, `exp` in the past).
2. `GET /auth/me` with it.
3. `GET /users` with it.

## Expected result
- Both → **401** `{"detail":"Access token has expired."}` — `jwt.decode` raises
  `ExpiredSignatureError`, which `decode_access_token` maps to `TokenExpiredError` *before*
  the generic `InvalidTokenError` branch (ordering matters because the former subclasses
  the latter).

## Harness
Script: `harness/tc_027.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_027.py`

---

## Execution result

- **Run at:** 2026-05-31 08:43 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The expired token returned **401** with the *specific* expiry message
> `{"detail":"Access token has expired."}` on both `/auth/me` and `/users` — distinct from
> the generic "invalid" message, confirming the expiry branch (not the catch-all) handled it.

**Evidence**

```
[forge] expired company token (iat/exp in the past, valid signature)
[attack] GET /auth/me (expired token) -> 401 {"detail":"Access token has expired."}
[attack] GET /users (expired token) -> 401 {"detail":"Access token has expired."}
```

**Verdict**

Defense **held**. `jwt.decode` enforces `exp`; `decode_access_token` catches
`jwt.ExpiredSignatureError` *first* (`security/tokens.py:85-86`) → `TokenExpiredError` →
401 (`error_handlers.py:39`). The distinct "has expired" message proves the ordered-except
handling works (expiry is not swallowed by the generic `InvalidTokenError` branch). This
empirically confirms audit §5 fix #4 (exp/aud/sub required, expiry enforced) under live
conditions — the pre-flagged CONFIRMS-FIXED hypothesis.

**Notes / follow-up**

Pairs with TC-IA-028 (a token *missing* `exp` is rejected too, so an attacker cannot dodge
this by simply omitting the claim).
