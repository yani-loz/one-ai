<!-- PAZ suite — Platform token-validation matrix (401 not 403/500). -->

# TC-PC-036: Malformed `sub` (not a UUID) → 401, NOT 500

| Field | Value |
|---|---|
| **ID** | TC-PC-036 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PAZ — Platform token-validation matrix |
| **Type** | Fuzz |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove a validly-signed, correct-audience platform token whose `sub` is **not a UUID** is rejected
with **401**, not a **500** — i.e. `_principal_from_claims` catches the `UUID(...)` `ValueError`
and converts it to `TokenInvalidError` instead of letting it bubble as an unhandled 500.

## Break hypothesis
If `UUID(str(claims["sub"]))` were not wrapped in the `except (KeyError, ValueError)`, a garbage
`sub` would raise `ValueError` deep in the dependency, surfacing as an opaque **500** (info leak +
broken contract). The bet: a non-UUID sub returns 500 (or, worse, is coerced and authenticates).

## Preconditions
- Live stack up. Token forged via `forge_platform_token(sub='not-a-uuid')` — valid signature,
  `aud='platform'`, all required claims present; only `sub` is malformed. Demo admin untouched.

## Steps
1. Forge a platform token with `sub='not-a-uuid'`.
2. `GET /platform/me`; record status + body.

## Expected result
- HTTP **401**, body `{"detail": "Access token claims are malformed."}` (the
  `_principal_from_claims` ValueError→TokenInvalidError catch). Explicitly **not 500**.

## Harness
Script: `harness/tc_036.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_036.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The token passed signature + audience + required-claim checks (so decode succeeded), then the
> non-UUID `sub` was caught during Principal construction and returned **401** with the
> claims-malformed message — no 500, no stack-trace leak.

**Evidence**

```
forged sub='not-a-uuid'
MALFORMED-SUB /platform/me -> 401 {"detail":"Access token claims are malformed."}
assert_401_not_500: PASS (got 401)
```

**Verdict**
Defense held. Code path: `_principal_from_claims`
(`backend/app/identity/dependencies.py:60-66`) wraps `UUID(str(claims["sub"]))` in
`try/except (KeyError, ValueError)` and raises `TokenInvalidError("Access token claims are
malformed.")` → 401. The distinct message (vs the generic "Access token is invalid.") proves the
post-decode application-layer catch fired, not the PyJWT decoder — confirming the documented
no-500 invariant on the dependency.

**Notes / follow-up**
This is the application-layer fuzz counterpart to TC-PC-037 (decoder-layer garbage). Both prove
the gate degrades to 401, never 500, on hostile structural input.
