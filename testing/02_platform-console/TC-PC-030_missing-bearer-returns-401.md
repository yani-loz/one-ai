<!--
  PAZ suite — Platform token-validation matrix (401 not 403/500).
  Authored top-half BEFORE running; Execution result block written back AFTER.
-->

# TC-PC-030: Missing Authorization header on `GET /platform/me` → 401 (not 403)

| Field | Value |
|---|---|
| **ID** | TC-PC-030 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PAZ — Platform token-validation matrix |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the token-gated DB-touch `GET /platform/me` rejects a request with **no** `Authorization`
header with **401 Unauthorized**, never the FastAPI/`HTTPBearer` default **403** (PC-02-AC8).

## Break hypothesis
If `HTTPBearer` were left at its `auto_error=True` default, a missing header would short-circuit
inside the security scheme and return **403** before the route's own `TokenInvalidError` (401)
path ever runs — leaking the wrong status and breaking the documented contract. The bet: some
path yields 403 (or a 500) instead of a clean 401.

## Preconditions
- Live stack up (`docker compose up`), `/health` → `database: reachable`.
- Run-stamp namespace: PAZ + `stamp()`; this case creates no org/email (pure header probe), so
  it touches no demo data and never mutates `super@ethera.ai`.

## Steps
1. `GET /platform/me` with **no** `Authorization` header.
2. Record the status code and JSON body.

## Expected result
- HTTP **401**.
- Body `{"detail": "Missing bearer token."}` (the `credentials is None` branch in
  `dependencies.get_current_platform_admin`).
- Never 403, never 500.

## Harness
Script: `harness/tc_030.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_030.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> `GET /platform/me` with no Authorization header returned **401** with the generic
> `Missing bearer token.` detail — not 403, not 500. The `auto_error=False` HTTPBearer plus the
> explicit `credentials is None` → `TokenInvalidError` branch holds live.

**Evidence**

```
NO-HEADER /platform/me -> 401 {"detail":"Missing bearer token."}
assert_401_not_403_500: PASS
```

**Verdict**
Defense held. PC-02-AC8 confirmed live. Code path: `dependencies.get_current_platform_admin`
(`backend/app/identity/dependencies.py:114-115` — `if credentials is None: raise
TokenInvalidError`) mapped to 401 by `error_handlers._STATUS_BY_EXCEPTION[TokenInvalidError]`
(`backend/app/identity/error_handlers.py:38`). The `_bearer_scheme = HTTPBearer(auto_error=False)`
(`dependencies.py:51`) is what prevents the default 403.

**Notes / follow-up**
Re-proof of a known-good AC; tagged CONFIRMS-FIXED. The gate-on-other-endpoints variant is
TC-PC-038.
