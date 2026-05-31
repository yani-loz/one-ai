# TC-IA-023: Platform token on `/auth/me` + `/users` → 401 (wrong audience)

| Field | Value |
|---|---|
| **ID** | TC-IA-023 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the reverse domain split: a **real** platform token (`aud='platform'`) is rejected
on company surfaces (`GET /auth/me`, `GET /users`) with **401** — proving Ethera platform
staff can never read tenant content by reusing their platform token on company routes.

## Break hypothesis
The attacker's bet: company dependencies validate signature but not `aud`, so a real
platform token is accepted as a company principal — letting platform staff (or a stolen
platform token) read tenant user data. Expected failure surface is a 200 on either route.
Subtlety: the correct rejection is **401** (audience fails in `decode_access_token`),
not 403 — the company role gate is never reached.

## Preconditions
- Live stack. Real platform token from `super@ethera.ai` via `/platform/login`.

## Steps
1. Platform-login → real platform token (`aud='platform'`).
2. `GET /auth/me` with the platform token.
3. `GET /users` with the platform token.

## Expected result
- Both → **401** `{"detail":"Access token is invalid."}` — `get_current_principal`'s
  `decode_access_token(aud='company')` rejects the `aud='platform'` token before any role
  check. Specifically 401, not 403.

## Harness
Script: `harness/tc_023.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_023.py`

---

## Execution result

- **Run at:** 2026-05-31 08:42 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> A real platform token (`aud='platform'`) returned **401** `{"detail":"Access token is
> invalid."}` on both `GET /auth/me` and `GET /users` — not 403, confirming the rejection
> happens at the audience check, before the role gate.

**Evidence**

```
[setup] platform login -> obtained real platform token (aud=platform)
[attack] GET /auth/me (platform token) -> 401 {"detail":"Access token is invalid."}
[attack] GET /users (platform token) -> 401 {"detail":"Access token is invalid."}
```

**Verdict**

Defense **held**. `get_current_principal` (`dependencies.py:72-87`) →
`decode_access_token(..., COMPANY_AUDIENCE)`; PyJWT's `audience='company'` enforcement
(`security/tokens.py:77-83`) rejects the `aud='platform'` token as `InvalidTokenError` →
`TokenInvalidError` → 401. The 401 (not 403) confirms the audience check fires before the
`require_company_admin` role gate. Completes the bidirectional proof of the
platform/company domain separation (the mirror of TC-IA-022); empirically confirms the
`FIX_BEFORE_PROD.md` separation invariant and audit §5.

**Notes / follow-up**

Platform staff therefore cannot read tenant content by token reuse — the product's data
sovereignty promise holds at the token-audience layer.
