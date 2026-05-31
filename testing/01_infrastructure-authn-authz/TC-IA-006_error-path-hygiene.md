# TC-IA-006: Error paths (404/405/422) leak no stack/secret

| Field | Value |
|---|---|
| **ID** | TC-IA-006 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Infrastructure |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
The standard error paths must return the correct status (404 unknown route, 405 wrong
method, 422 malformed JSON / wrong content-type) and **never** leak a stack trace, the JWT
secret, the DB DSN / Postgres password, or driver/ORM internals in the response body.

## Break hypothesis
A misconfigured debug mode or an unhandled exception leaks internals: a Python traceback,
the `dev-only-insecure-secret-change-me-in-prod` JWT secret, the `postgresql+asyncpg://…`
DSN (with the `oneai` password), or `sqlalchemy.exc`/`asyncpg` class names — any of which
hands an attacker reconnaissance or the forging secret outright.

## Preconditions
Live stack. No data setup. Namespace: `infra-<stamp>` (no data provisioned). Request bodies
are **benign** (no secret-like strings) so the leak scan cannot false-positive on our own
input.

## Steps
1. `GET /this-route-does-not-exist-xyz` → expect 404.
2. `DELETE /health` (wrong method) → expect 405.
3. `POST /auth/login` with malformed JSON (`{not-valid-json`) → expect 422.
4. `POST /auth/login` with wrong `Content-Type: text/plain` → expect 422.
5. Scan every error body for leak markers: the JWT secret, `postgresql+asyncpg`,
   `postgres://`, `:oneai@`, `Traceback (most recent call last)`, `File "/app`,
   `sqlalchemy.exc`, `asyncpg`.

## Expected result
Statuses 404 / 405 / 422 / 422 respectively; every body is a terse `{"detail": …}` (or
Pydantic validation list) with **zero** leak markers.

## Harness
Script: `harness/tc_006.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_006.py`

---

## Execution result

- **Run at:** 2026-05-31 11:52 local
- **Result:** ✅ Pass
- **Finding tag:** — (negative contract test; no defect found)

**Actual behavior**

> All four probes returned the expected status (404 / 405 / 422 / 422) with terse JSON
> bodies. No JWT secret, DSN, Postgres password, traceback, or ORM/driver internal appeared
> in any body. The wrong-content-type probe (D) echoes the benign input `email=x&password=y`
> by Pydantic design — no sensitive data, and our input carried none.

**Evidence**

```
=== A unknown route GET /this-route-does-not-exist-xyz ===
  status: 404 (expected 404 )
  status matches: True
  content-type: application/json
  body: {"detail":"Not Found"}
  LEAK MARKERS FOUND: none
=== B wrong method DELETE /health ===
  status: 405 (expected 405 )
  status matches: True
  content-type: application/json
  body: {"detail":"Method Not Allowed"}
  LEAK MARKERS FOUND: none
=== C malformed JSON POST /auth/login ===
  status: 422 (expected 422 )
  status matches: True
  content-type: application/json
  body: {"detail":[{"type":"json_invalid","loc":["body",1],"msg":"JSON decode error","input":{},"ctx":{"error":"Expecting property name enclosed in double quotes"}}]}
  LEAK MARKERS FOUND: none
=== D wrong Content-Type POST /auth/login ===
  status: 422 (expected 422 )
  status matches: True
  content-type: application/json
  body: {"detail":[{"type":"model_attributes_type","loc":["body"],"msg":"Input should be a valid dictionary or object to extract fields from","input":"email=x&password=y"}]}
  LEAK MARKERS FOUND: none
=== SUMMARY ===
any secret/stack/DSN leak across all error bodies: False
```

**Verdict**

Defense held. FastAPI's default 404/405 responses and Pydantic's 422 validation envelopes
are terse and structural; the identity exception handlers
(`backend/app/identity/error_handlers.py:68-69`) emit only `{"detail": str(exc)}` with
already-generic messages. No leak across any of the four error surfaces. Severity Medium
applied only if a leak had been found — none was.

**Notes / follow-up**

Pydantic's 422 echoes the offending `input` field (visible in probe D) — this is framework
behavior and benign for these surfaces, but worth noting for input-validation cases
(TC-IA-06x) where a sensitive field (e.g. a password) could be reflected; those cases should
confirm the `password` field is never echoed in a 422 body. No action for this case.
