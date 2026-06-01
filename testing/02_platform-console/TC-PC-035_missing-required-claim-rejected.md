<!-- PAZ suite — Platform token-validation matrix (401 not 403/500). -->

# TC-PC-035: Missing required claim (`aud`/`exp`/`sub`) → 401 each

| Field | Value |
|---|---|
| **ID** | TC-PC-035 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PAZ — Platform token-validation matrix |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove the decoder's `options={"require": ["exp", "aud", "sub"]}` is enforced: a validly-signed
platform token **missing any one** of `exp`, `aud`, or `sub` is rejected with 401 — in particular
a missing `exp` must NOT be silently treated as a non-expiring token.

## Break hypothesis
If `require` were absent, a token dropping `exp` would be accepted as never-expiring (a forever
token), or one dropping `aud` would skip the audience binding (cross-domain leak). The bet: at
least one drop authenticates (200) or 500s.

## Preconditions
- Live stack up. Three tokens forged via `forge_platform_token(sub=<real demo admin>,
  drop=(<claim>,))` — each otherwise valid (correct secret, signed). Demo admin untouched.

## Steps
1. For each of `aud`, `exp`, `sub`: forge a platform token with that claim dropped.
2. `GET /platform/me` with each; record status, body, and the surviving claim set.

## Expected result
- All three → **401**, body `{"detail": "Access token is invalid."}` (PyJWT
  `MissingRequiredClaimError ⊂ InvalidTokenError`). Never 200, never 500.

## Harness
Script: `harness/tc_035.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_035.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Each forged token was missing exactly one required claim (confirmed by the printed surviving
> claim set). All three were rejected with **401** — including the critical `exp`-dropped token
> (not treated as non-expiring) and the `aud`-dropped token (audience binding not skipped).

**Evidence**

```
DROP 'aud' (claims present=['sub', 'type', 'role', 'org_id', 'iat', 'exp', 'jti']) /platform/me -> 401 {"detail":"Access token is invalid."}
DROP 'exp' (claims present=['sub', 'type', 'aud', 'role', 'org_id', 'iat', 'jti']) /platform/me -> 401 {"detail":"Access token is invalid."}
DROP 'sub' (claims present=['type', 'aud', 'role', 'org_id', 'iat', 'exp', 'jti']) /platform/me -> 401 {"detail":"Access token is invalid."}
assert_all_401: PASS {'aud': 401, 'exp': 401, 'sub': 401}
```

**Verdict**
Defense held. Code path: `decode_access_token` (`backend/app/identity/security/tokens.py:82`)
passes `options={"require": ["exp", "aud", "sub"]}`; a missing required claim raises
`MissingRequiredClaimError ⊂ InvalidTokenError` → `TokenInvalidError` → 401. Confirms the
file's documented invariant "a token missing exp is rejected, not silently treated as
non-expiring."

**Notes / follow-up**
The `aud`-drop is doubly defended: even without `require`, PyJWT errors when an `audience=` is
expected but the token has none. This case proves the explicit `require` list regardless.
