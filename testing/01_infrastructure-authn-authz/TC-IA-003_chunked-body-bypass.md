# TC-IA-003: Chunked / no-Content-Length large body bypasses the cap

| Field | Value |
|---|---|
| **ID** | TC-IA-003 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Infrastructure |
| **Type** | Adversarial |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Characterize the documented limitation of `MaxBodySizeMiddleware`: it only inspects the
**declared** `Content-Length` header, so a `Transfer-Encoding: chunked` body with **no**
`Content-Length` is *not* capped — the >1 MiB payload is buffered and the route runs.

## Break hypothesis
Streaming the body via an async generator forces httpx into chunked transfer with no
`Content-Length`; the guard's `content_length is not None` check (`middleware.py:30-32`)
is skipped, so the same ~2 MiB body that earns a 413 in TC-IA-002 now passes the middleware
and is processed by the route. Proof = status is **not 413** (we expect 422 on invalid
JSON, which proves the oversized body reached the route).

## Preconditions
Live stack. No data setup. Namespace: `infra-<stamp>` (no data provisioned).
Body: ~2 MiB of `A` bytes streamed in 256 KiB chunks via an async generator, with a
JSON content-type but invalid JSON content.

## Steps
1. Build `POST /auth/login` with `content=<async generator>` and `content-type: application/json`.
2. Confirm the outgoing request has **no** `content-length` and `transfer-encoding: chunked`.
3. Send; assert status != 413 (and == 422, proving the body was parsed by the route).

## Expected result
Status **422** (JSON decode error) — i.e. the >1 MiB chunked body was **not** rejected by
the cap, demonstrating the documented bypass. (Contrast: TC-IA-002, same size *with*
`Content-Length`, returns 413.)

## Harness
Script: `harness/tc_003.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_003.py`

---

## Execution result

- **Run at:** 2026-05-31 11:50 local
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> A 2 MiB chunked body (no `Content-Length`) was **not** rejected with 413; it was buffered
> and parsed by the route, returning 422 (JSON decode error). The cap was bypassed exactly
> as the middleware docstring documents.

**Evidence**

```
=== POST /auth/login (chunked, NO declared Content-Length) ===
declared content-length header present: False
transfer-encoding header: chunked
intended body size: 2097152 bytes (cap=1048576)
status: 422
body: {"detail":[{"type":"json_invalid","loc":["body",0],"msg":"JSON decode error","input":{},"ctx":{"error":"Expecting value"}}]}
--- assertions ---
status != 413 (cap bypassed): True
body was processed by route (422 invalid-JSON expected): True
```

Contrast control (TC-IA-002, same ~2 MiB body **with** declared Content-Length): status
413, `{"detail":"Request body too large."}`.

**Verdict**

Behaves **as documented** — not a new break. `MaxBodySizeMiddleware.dispatch`
(`backend/app/core/middleware.py:30-32`) guards only `request.headers.get("content-length")`;
a chunked/no-CL body skips the branch entirely, so the full body is buffered before the
route validates it. The module docstring (`middleware.py:7-9`) explicitly states this and
defers full enforcement to the reverse proxy. Severity Low: the auth route sha256's the
body to a fixed length and never matches an oversized value, so the only cost is wasted
buffering (a bounded DoS amplification, not a data path). Confirms the documented deferral.

**Notes / follow-up**

Production remediation is `client_max_body_size` (or equivalent) at the ingress/reverse
proxy, per the middleware docstring and the FIX_BEFORE_PROD ops-posture section — the app
middleware is intentionally a coarse, declared-CL-only guard. Pairs with TC-IA-002 as the
with-CL / no-CL contrast.
