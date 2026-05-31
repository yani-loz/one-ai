# TC-IA-025: `alg=none` (unsigned) token → 401

| Field | Value |
|---|---|
| **ID** | TC-IA-025 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the classic JWT `alg=none` downgrade attack is rejected: an **unsigned** token
(algorithm `none`, no signature) on `/auth/me` must be 401, because `decode_access_token`
pins `algorithms=['HS256']` and so refuses the `none` algorithm.

## Break hypothesis
The attacker's bet: the decoder accepts the `alg` value from the token header instead of
pinning HS256, so an attacker forges a fully-controlled unsigned token (any `sub`/`org_id`/
`role`) with **no secret** and is authenticated. This is the single most dangerous JWT
flaw — a 200 here = total auth bypass for any identity, hence Critical.

## Preconditions
- Live stack. No login needed — the token is forged client-side with `alg='none'` and an
  empty signature; `org_id`/`sub` are arbitrary fresh UUIDs.

## Steps
1. Forge a company token with `forge_company_token(alg='none', ...)` (header `{"alg":"none"}`,
   trailing empty signature segment).
2. `GET /auth/me` with the unsigned token.
3. `GET /users` with the same token (admin-only surface) for thoroughness.

## Expected result
- Both → **401** `{"detail":"Access token is invalid."}` — PyJWT raises
  `InvalidAlgorithmError` (a subclass of `InvalidTokenError`) because `none` is not in the
  allowed `algorithms` list. No principal is ever constructed.

## Harness
Script: `harness/tc_025.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_025.py`

---

## Execution result

- **Run at:** 2026-05-31 08:42 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The unsigned `alg=none` token (header `eyJhbGciOiJub25lIi...` = `{"alg":"none","typ":"JWT"}`)
> returned **401** `{"detail":"Access token is invalid."}` on both `/auth/me` and `/users`.
> The forged identity was never accepted.

**Evidence**

```
[forge] alg=none token -> eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiI0Y2UzZWNmYS1i...
[attack] GET /auth/me (alg=none token) -> 401 {"detail":"Access token is invalid."}
[attack] GET /users (alg=none token) -> 401 {"detail":"Access token is invalid."}
```

**Verdict**

Defense **held**. `decode_access_token` passes `algorithms=[settings.jwt_algorithm]`
(= `['HS256']`, `security/tokens.py:80`); PyJWT rejects the `none` algorithm as
`InvalidAlgorithmError` → `InvalidTokenError` → `TokenInvalidError` → 401
(`tokens.py:87-88`, `error_handlers.py:38`). The `alg=none` downgrade is closed; confirms
the pre-flagged CONFIRMS-FIXED hypothesis. No Critical bypass exists on this path.

**Notes / follow-up**

Note this holds even though the dev `JWT_SECRET` is the forgeable default — the algorithm
pin is an independent control: a forged *HS256* token signed with the leaked dev secret is
a separate capability (tested in the cross-tenant forgery cases TC-IA-035/036).
