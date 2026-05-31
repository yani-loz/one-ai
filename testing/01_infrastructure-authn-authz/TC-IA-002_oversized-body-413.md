# TC-IA-002: Oversized body (declared Content-Length) → 413

| Field | Value |
|---|---|
| **ID** | TC-IA-002 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Infrastructure |
| **Type** | Adversarial |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`MaxBodySizeMiddleware` must reject any request whose **declared** `Content-Length`
exceeds `settings.max_request_body_bytes` (1 MiB) with **413** *before the route runs* —
the cheap DoS guard added as the body-size-cap remediation for audit **AUD-08**.

## Break hypothesis
The middleware fails to fire and the oversized body is buffered + parsed by the route
(yielding 422/401/500 instead of 413), or the cap is mis-read so a >1 MiB body slips
through. `httpx` auto-sets `Content-Length` for a `json=`/bytes body, so this exercises
the declared-CL path the guard checks (`middleware.py:30-32`).

## Preconditions
Live stack. No data setup (the oversized login never reaches credential matching).
Namespace: `infra-<stamp>` (no data provisioned). Payload: a ~2 MiB password string in a
JSON login body, so `Content-Length` ≈ 2,097,205 bytes (well past the 1,048,576 cap).

## Steps
1. Build `POST /auth/login` with `json={"email": ..., "password": "A"*2 MiB}`; read the
   `Content-Length` httpx set and assert it exceeds the cap.
2. Send the request.
3. Assert status == 413 and body `detail == "Request body too large."`.

## Expected result
`413` with `{"detail":"Request body too large."}`, produced by the middleware before the
`/auth/login` route executes.

## Harness
Script: `harness/tc_002.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_002.py`

---

## Execution result

- **Run at:** 2026-05-31 11:49 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> A 2,097,205-byte declared body returned 413 with the middleware's own detail message —
> the route never ran. The body-size cap (AUD-08 remediation) holds under a live request,
> confirming the audit's smoke-test claim ("oversized-body → 413").

**Evidence**

```
=== POST /auth/login (oversized body, declared Content-Length) ===
declared content-length: 2097205 bytes
max_request_body_bytes (cap): 1048576 bytes
declared CL > cap: True
status: 413
body: {"detail":"Request body too large."}
--- assertions ---
status==413: True
detail: Request body too large.
```

**Verdict**

Defense held. `MaxBodySizeMiddleware.dispatch` (`backend/app/core/middleware.py:28-36`)
returns 413 when the declared `Content-Length` exceeds the configured `max_bytes`,
registered outermost in `main.py:48`. This empirically **confirms the AUD-08 fix** (the
audit credited a global body-size cap as the remediation and listed "oversized-body → 413"
as a verified live smoke test; line 8 of `docs/audits/2026-05-30_identity-module-deep-audit.md`).

**Notes / follow-up**

This guard only covers the **declared** `Content-Length` path; the chunked / no-CL bypass
is the explicitly-documented gap exercised in **TC-IA-003**. Full enforcement still belongs
at the reverse proxy (`middleware.py` docstring; FIX_BEFORE_PROD ops posture).
