<!-- PAZ suite — Platform token-validation matrix (401 not 403/500). -->

# TC-PC-031: `alg=none` platform token on `GET /platform/me` → 401

| Field | Value |
|---|---|
| **ID** | TC-PC-031 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PAZ — Platform token-validation matrix |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove the classic JWT `alg=none` (unsigned) bypass is rejected: a token with header
`{"alg":"none"}` and `aud='platform'` must not authenticate `GET /platform/me`.

## Break hypothesis
If the verifier passed the algorithm through from the token header (instead of pinning
`algorithms=[HS256]`), an attacker could forge an unsigned `alg=none` token — no secret needed —
and read any platform admin's identity. The bet: an unsigned token authenticates (200) or 500s.

## Preconditions
- Live stack up. Token forged via `forge_platform_token(alg='none')` — no signing secret used.
- No org/email created; demo admin untouched.

## Steps
1. Forge `alg=none` platform token (`jwt.encode(claims, key=None, algorithm="none")`).
2. `GET /platform/me` with it as the bearer. Record status + body + the token's unverified header.

## Expected result
- HTTP **401**, body `{"detail": "Access token is invalid."}` (PyJWT raises
  `InvalidAlgorithmError ⊂ InvalidTokenError` because the decoder only allows `HS256`).
- Never 200, never 500.

## Harness
Script: `harness/tc_031.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_031.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The forged token genuinely carried header `{"alg":"none","typ":"JWT"}` (unsigned). The server
> rejected it with **401** — the algorithm allowlist held; the unsigned bypass does not work.

**Evidence**

```
ALG=NONE token (header alg): {'alg': 'none', 'typ': 'JWT'}
ALG=NONE /platform/me -> 401 {"detail":"Access token is invalid."}
assert_401: PASS (got 401)
```

**Verdict**
Defense held — the `alg=none` bypass is closed. Code path: `decode_access_token`
(`backend/app/identity/security/tokens.py:77-83`) pins `algorithms=[settings.jwt_algorithm]`
(HS256); the `none` algorithm is not in the allowlist so PyJWT raises an `InvalidTokenError`
subclass → `TokenInvalidError` (`tokens.py:87-88`) → 401. Tagged **CONFIRMS-FIXED**: this is the same
"cannot mint/mutate a token without the real secret" isolation property as TC-PC-032 (tampered
sig) and TC-PC-034 (wrong secret) — the textbook `alg=none` bypass against code that explicitly
pins `algorithms=[HS256]`, held on the first live hit, so it is a re-proof, not a fresh defect.

**Notes / follow-up**
The single isolation layer is the JWT secret (RLS inert); this confirms a missing-signature
attack cannot sidestep it. Pairs with TC-PC-034 (wrong secret) and TC-PC-032 (tampered sig).
