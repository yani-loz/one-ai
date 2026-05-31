# TC-IA-028: Token missing a required claim (exp / aud / sub) → 401

| Field | Value |
|---|---|
| **ID** | TC-IA-028 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Adversarial |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the decoder requires `exp`, `aud`, and `sub` to be present: a validly-signed token
missing any one of them is rejected with 401 — closing the "omit `exp` → non-expiring
token" and "omit `aud` → skip the domain check" evasions.

## Break hypothesis
The attacker's bet: required-claim enforcement is absent, so dropping `exp` yields a token
that never expires, dropping `aud` slips past the domain split, or dropping `sub` produces
a principal-less-but-accepted token. Any 200 on a drop variant is the defect.

## Preconditions
- Live stack. Three tokens forged with the real dev secret and `aud='company'`, each
  dropping exactly one required claim via `forge_company_token(drop=('exp',|'aud',|'sub',))`.

## Steps
1. Forge a token with `exp` dropped → `GET /auth/me`.
2. Forge a token with `aud` dropped → `GET /auth/me`.
3. Forge a token with `sub` dropped → `GET /auth/me`.

## Expected result
- All three → **401** `{"detail":"Access token is invalid."}` — `decode_access_token`
  passes `options={"require": ["exp","aud","sub"]}`, so PyJWT raises
  `MissingRequiredClaimError` (subclass of `InvalidTokenError`) for each. (Note: a dropped
  `aud` also fails the `audience=` match; either way it is 401.)

## Harness
Script: `harness/tc_028.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_028.py`

---

## Execution result

- **Run at:** 2026-05-31 08:43 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Each token missing one of `exp` / `aud` / `sub` returned **401** `{"detail":"Access token
> is invalid."}` on `/auth/me`. No drop variant was accepted.

**Evidence**

```
[attack] GET /auth/me (drop 'exp') -> 401 {"detail":"Access token is invalid."}
[attack] GET /auth/me (drop 'aud') -> 401 {"detail":"Access token is invalid."}
[attack] GET /auth/me (drop 'sub') -> 401 {"detail":"Access token is invalid."}
```

**Verdict**

Defense **held**. `decode_access_token` enforces presence via
`options={"require": ["exp","aud","sub"]}` (`security/tokens.py:82`); PyJWT raises
`MissingRequiredClaimError` → `InvalidTokenError` → `TokenInvalidError` → 401. Crucially,
the dropped-`exp` case is rejected (not treated as a non-expiring token), which is the
exact guarantee audit §5 fix #4 claims. Confirms the pre-flagged CONFIRMS-FIXED hypothesis.

**Notes / follow-up**

Complements TC-IA-027: an attacker can neither present an expired token nor evade expiry by
omitting the claim. The required-claim set is the structural backstop behind the expiry and
audience controls.
