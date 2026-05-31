# TC-IA-022: Company token on `/platform/orgs` (GET + POST) → 401 (wrong audience)

| Field | Value |
|---|---|
| **ID** | TC-IA-022 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the two-audience domain split: a **real** company access token (`aud='company'`)
is rejected on the platform-admin surface (`GET`/`POST /platform/orgs`) with 401, so a
compromised company account can never reach the cross-org platform domain.

## Break hypothesis
The attacker's bet: the platform gate validates the signature but not the `aud` claim,
so any validly-signed company token is accepted as a platform admin — a privilege-domain
crossing that would let a single tenant admin onboard/enumerate all orgs. A 200 (GET) or
201 (POST) is the catastrophic outcome.

## Preconditions
- Live stack. Fresh run-stamped org `authz-<stamp>` onboarded via the platform admin.
- Admin `authz-admin-<stamp>@example.com` logged in → real company token (`aud='company'`).

## Steps
1. Platform-login; onboard a fresh org; admin logs in → real company token.
2. `GET /platform/orgs` with the company token.
3. `POST /platform/orgs` with the company token and a valid, schema-complete body.

## Expected result
- Both → **401** `{"detail":"Access token is invalid."}` — the audience check inside
  `decode_access_token(aud='platform')` fails for an `aud='company'` token; the
  platform-admin dependency is never satisfied. No org created.

## Harness
Script: `harness/tc_022.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_022.py`

---

## Execution result

- **Run at:** 2026-05-31 08:42 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> A real company token (`aud='company'`) returned **401** `{"detail":"Access token is
> invalid."}` on both `GET /platform/orgs` and `POST /platform/orgs`. No organization was
> onboarded.

**Evidence**

```
[setup] onboard_org -> 201
[setup] company admin login -> 200 (aud=company)
[attack] GET /platform/orgs (company token) -> 401 {"detail":"Access token is invalid."}
[attack] POST /platform/orgs (company token) -> 401 {"detail":"Access token is invalid."}
```

**Verdict**

Defense **held**. `get_current_platform_admin`
(`dependencies.py:103-117`) calls `decode_access_token(..., PLATFORM_AUDIENCE)`; PyJWT's
`audience='platform'` enforcement (`security/tokens.py:77-83`) rejects the `aud='company'`
token as `InvalidTokenError` → `TokenInvalidError` → 401 (`error_handlers.py:38`). This
empirically confirms the `FIX_BEFORE_PROD.md` "keep platform-admin auth physically
separate" invariant in the company→platform direction. The mirror direction is TC-IA-023.

**Notes / follow-up**

Confirms audit verification §5 (audience binds the two domains) under live conditions in
the company→platform direction. The valid POST body removed any chance a 422 masked the
401 — auth resolves before body validation.
