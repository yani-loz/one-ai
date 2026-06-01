<!-- PAZ suite — Platform token-validation matrix (401 not 403/500). -->

# TC-PC-037: Garbage bearer strings → 401, NOT 500

| Field | Value |
|---|---|
| **ID** | TC-PC-037 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PAZ — Platform token-validation matrix |
| **Type** | Fuzz |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove non-JWT bearer payloads — a malformed `not.a.jwt` (dotted but not a real JWT) and a random
opaque string (no dots) — are rejected with **401**, not a **500** from the decoder choking on
unparseable input.

## Break hypothesis
If the decode path let a `DecodeError`/base64/structural exception escape unmapped, garbage bearer
input would surface as **500** (and a parser crash is a soft-DoS surface). The bet: at least one
garbage string returns 500.

## Preconditions
- Live stack up. Two probes sent as raw `Authorization: Bearer <garbage>`. No token forging, no
  org/email created; demo admin untouched.

## Steps
1. `GET /platform/me` with `Authorization: Bearer not.a.jwt`.
2. `GET /platform/me` with `Authorization: Bearer <random opaque string>`.
3. Record both statuses + bodies.

## Expected result
- Both → **401**, body `{"detail": "Access token is invalid."}` (PyJWT `DecodeError ⊂
  InvalidTokenError`). Never 500.

## Harness
Script: `harness/tc_037.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_037.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Both a dotted-but-invalid string and a dot-free opaque blob were rejected with **401** and the
> generic invalid-token detail — the decoder mapped the parse failure cleanly, no 500.

**Evidence**

```
GARBAGE 'Bearer not.a.jwt' /platform/me -> 401 {"detail":"Access token is invalid."}
GARBAGE 'random opaque string' /platform/me -> 401 {"detail":"Access token is invalid."}
assert_all_401_not_500: PASS {'Bearer not.a.jwt': 401, 'random opaque string': 401}
```

**Verdict**
Defense held. Code path: `decode_access_token` (`backend/app/identity/security/tokens.py:87-88`)
catches the broad `jwt.InvalidTokenError` (which `DecodeError` subclasses) and raises
`TokenInvalidError` → 401. The unparseable input never reaches a 500.

**Notes / follow-up**
Decoder-layer counterpart to TC-PC-036 (post-decode application-layer fuzz). Together they show
the token gate is total: every structural failure mode resolves to 401.
