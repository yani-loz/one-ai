# TC-IA-005: CORS — credentialed wildcard methods/headers posture

| Field | Value |
|---|---|
| **ID** | TC-IA-005 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Infrastructure |
| **Type** | Adversarial |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Characterize the CORS posture: the allowlist on `allow_origins` (the *defense*) holds —
a hostile origin is not reflected — while documenting the tracked breadth concern:
`allow_credentials=True` combined with `allow_methods=["*"]` / `allow_headers=["*"]`
(FIX_BEFORE_PROD "Lock CORS").

## Break hypothesis
The real risk (a NEW finding) would be the allowlist failing open: an arbitrary origin
(`http://evil.example`) getting echoed in `Access-Control-Allow-Origin` alongside
`Access-Control-Allow-Credentials: true`, which would let any website make credentialed
cross-origin calls. The documented (non-break) concern is that the methods/headers are
wildcarded for the *allowed* origin — broader than the real surface needs.

## Preconditions
Live stack. No data setup. Namespace: `infra-<stamp>` (no data provisioned).
A real preflight requires **both** `Origin` and `Access-Control-Request-Method` headers
(without the latter Starlette ignores it as a non-CORS request).

## Steps
1. Preflight `OPTIONS /auth/login` with `Origin: http://evil.example` +
   `Access-Control-Request-Method: POST`. Inspect `Access-Control-Allow-*`.
2. Preflight `OPTIONS /auth/login` with `Origin: http://localhost:5173` (the configured
   frontend) + `Access-Control-Request-Method: POST`. Inspect the same headers.

## Expected result
- Evil origin: **no** `Access-Control-Allow-Origin: http://evil.example` echoed (allowlist
  rejects it).
- Allowed origin: ACAO echoes `http://localhost:5173`, `Allow-Credentials: true`, and the
  methods/headers reflect the wildcard expansion (the documented breadth concern).

## Harness
Script: `harness/tc_005.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_005.py`

---

## Execution result

- **Run at:** 2026-05-31 11:52 local
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> The allowlist held: the hostile origin got **no** `Access-Control-Allow-Origin` header
> (status 400, ACAO absent), so `evil.example` cannot make credentialed cross-origin calls.
> The allowed origin received `ACAO: http://localhost:5173` + `Allow-Credentials: true` +
> the full wildcard-expanded method list (`DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT`) —
> the documented credentialed-wildcard breadth.

**Evidence**

```
=== Preflight OPTIONS /auth/login | Origin: http://evil.example ===
status: 400
  access-control-allow-origin: None
  access-control-allow-credentials: true
  access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
  access-control-allow-headers: content-type
  vary: Origin
evil origin reflected in ACAO: False
=== Preflight OPTIONS /auth/login | Origin: http://localhost:5173 ===
status: 200
  access-control-allow-origin: http://localhost:5173
  access-control-allow-credentials: true
  access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
  access-control-allow-headers: content-type,authorization
  vary: Origin
--- assessment ---
allowed origin echoed: True
credentials allowed: True
methods reflected (wildcard concern): DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
headers reflected (wildcard concern): content-type,authorization
```

**Verdict**

Defense held — the **origin allowlist is the security boundary and it works**: an arbitrary
origin is not granted `Access-Control-Allow-Origin`, so the credentialed-wildcard posture
cannot be abused by an untrusted site. (Note: for the hostile-origin probe, Starlette still
emits the static `Allow-Credentials`/`Allow-Methods` defaults, but **without** ACAO the
browser blocks the response — the missing ACAO is what enforces isolation.) The documented
concern is real but bounded: for the *allowed* origin, `allow_methods=["*"]` + `allow_headers=["*"]`
with `allow_credentials=True` (`backend/app/main.py:40-46`) is broader than the real surface
needs. This **confirms the documented "Lock CORS" item** (`docs/FIX_BEFORE_PROD.md:88`),
which calls to tighten methods/headers and pin origins — not a new break.

**Notes / follow-up**

Remediation (tracked) per FIX_BEFORE_PROD line 88: replace the method/header wildcards with
the actual surface (e.g. `GET, POST, PATCH, DELETE, OPTIONS` and the specific headers) and
pin `cors_origins` to the production frontend domains. The allowlist itself already being
env-restricted is the reason this is Low, not a NEW finding.
