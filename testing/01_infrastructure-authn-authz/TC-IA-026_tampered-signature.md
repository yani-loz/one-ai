# TC-IA-026: Tampered signature (flip last char) → 401

| Field | Value |
|---|---|
| **ID** | TC-IA-026 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify HMAC signature verification is actually enforced: take a **real, valid** company
token and flip its last signature character — the decoder must reject it with 401, proving
signatures are checked rather than trusted.

## Break hypothesis
The attacker's bet: signature verification is disabled or bypassed (e.g. `verify=False`
slipped in), so any structurally-valid JWT is accepted regardless of signature — letting
an attacker edit claims (role, org_id) on a captured token and re-sign-by-mutation. A 200
on the tampered token = signatures not enforced = Critical.

## Preconditions
- Live stack. Fresh run-stamped org `authz-<stamp>` onboarded; admin logged in → a real,
  valid `aud='company'` token to mutate. Control request proves the unmodified token works.

## Steps
1. Onboard a fresh org; admin logs in → real token.
2. Control: `GET /auth/me` with the real token (must be 200).
3. Flip the final character of the token (last byte of the signature segment).
4. `GET /auth/me` with the tampered token.

## Expected result
- Control → **200**.
- Tampered → **401** `{"detail":"Access token is invalid."}` — `jwt.decode` raises
  `InvalidSignatureError` (subclass of `InvalidTokenError`).

## Harness
Script: `harness/tc_026.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_026.py`

---

## Execution result

- **Run at:** 2026-05-31 08:43 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The unmodified token returned **200** on `/auth/me`; after flipping the last signature
> char (`'g'` → `'A'`), the same-claims token returned **401** `{"detail":"Access token is
> invalid."}`. Signature verification is enforced.

**Evidence**

```
[setup] onboard_org -> 201
[setup] admin login -> 200
[control] GET /auth/me (real token) -> 200
[tamper] last sig char 'g' -> 'A'
[attack] GET /auth/me (tampered signature) -> 401 {"detail":"Access token is invalid."}
```

**Verdict**

Defense **held**. `decode_access_token` (`security/tokens.py:77-83`) calls `jwt.decode`
with the secret and no `verify=False`, so a mutated signature raises
`InvalidSignatureError` → `InvalidTokenError` → `TokenInvalidError` → 401. The
control 200 on the same claims proves the only difference rejected was the signature byte.
HMAC integrity is enforced; confirms the pre-flagged CONFIRMS-FIXED hypothesis.

**Notes / follow-up**

This is the integrity control that makes the dev-secret risk *quantitative*: an attacker
cannot mutate a captured token, but with the leaked dev `JWT_SECRET` they can sign a fresh
one — that distinct capability is covered by the forgery cases, not this one.
