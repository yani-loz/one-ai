# TC-IA-024: Missing Authorization header → 401 (not 403)

| Field | Value |
|---|---|
| **ID** | TC-IA-024 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Negative |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify that an entirely unauthenticated request (no `Authorization` header) on protected
company and platform endpoints returns **401**, not FastAPI `HTTPBearer`'s default 403 —
the SPEC §4 "401 for no/invalid token" contract that `auto_error=False` exists to enforce.

## Break hypothesis
The attacker's bet: the bearer scheme uses the library default (`auto_error=True`), so a
missing header yields a 403 — the wrong code (403 implies "authenticated but forbidden",
muddling the auth state and the SPA's redirect logic). A non-401 here is the defect.

## Preconditions
- Live stack. No setup/tokens needed — these are unauthenticated probes.

## Steps
1. `GET /auth/me` with no `Authorization` header.
2. `GET /users` with no `Authorization` header.
3. `GET /platform/orgs` with no `Authorization` header.

## Expected result
- All three → **401** `{"detail":"Missing bearer token."}` (the dependency raises
  `TokenInvalidError` when `credentials is None`), never 403.

## Harness
Script: `harness/tc_024.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_024.py`

---

## Execution result

- **Run at:** 2026-05-31 08:42 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> All three protected GET endpoints returned **401** `{"detail":"Missing bearer token."}`
> with no `Authorization` header — not 403.

**Evidence**

```
[attack] GET /auth/me (no auth header) -> 401 {"detail":"Missing bearer token."}
[attack] GET /users (no auth header) -> 401 {"detail":"Missing bearer token."}
[attack] GET /platform/orgs (no auth header) -> 401 {"detail":"Missing bearer token."}
```

**Verdict**

Defense **held**. `_bearer_scheme = HTTPBearer(auto_error=False)`
(`dependencies.py:51`) yields `credentials=None` on a missing header; both
`get_current_principal` (`:84-85`) and `get_current_platform_admin` (`:114-115`) raise
`TokenInvalidError` → 401 (`error_handlers.py:38`). The library's default 403 is
correctly suppressed. Confirms the SPEC §4 / pre-flagged CONFIRMS-FIXED hypothesis.

**Notes / follow-up**

GET variants were used for all three endpoints so no request body is involved and the
401 cannot be confused with a body-validation 422.
