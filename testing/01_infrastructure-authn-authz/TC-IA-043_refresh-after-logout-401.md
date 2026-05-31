<!--
  Test-case: TC-IA-043. See ../README.md for legend, tags, severity scale.
-->

# TC-IA-043: Refresh after logout → 401

| Field | Value |
|---|---|
| **ID** | TC-IA-043 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Token lifecycle |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Logout durably revokes a refresh token: after `POST /auth/logout`, attempting to rotate
the SAME token via `POST /auth/refresh` must fail with `401 RefreshTokenInvalid`. The
logout revoke and the refresh-consume guard share the same `revoked_at IS NULL` storage
state, so a logged-out token is already revoked and `consume()` rejects it.

## Break hypothesis
If logout did not persist the revoke (in-memory only / rolled back), the token would
still rotate after logout → a `200` pair. The bet: refresh-after-logout yields `200`.

## Preconditions
- Live stack; fresh run-stamped org `token-<stamp>` + admin logged in.

## Steps
1. Onboard a fresh org; login as admin → capture refresh R0.
2. `POST /auth/logout` with R0 → expect `204`.
3. `POST /auth/refresh` with R0 (now revoked by logout) → expect `401`.

## Expected result
- Step 2: `204`.
- Step 3: `401` with `{"detail":"Refresh token is invalid."}`.

## Harness
Script: `harness/tc_043.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_043.py`

---

## Execution result

- **Run at:** 2026-05-31 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Logout of R0 returned `204`; the subsequent refresh of the same R0 returned
> `401 {"detail":"Refresh token is invalid."}`. Logout durably (committed) revokes the
> refresh token so it can no longer be rotated.

**Evidence**

```
[setup] namespace=token-19e7d32faf11bc6 slug=token-19e7d32faf11bc6 admin=admin-19e7d32faf11bc6@token.example.com
[setup] onboard_org -> 201
[setup] login -> 200  R0(prefix)=ty7KF0EcOESD...
[step2] logout(R0) -> 204  body=''
[step3] refresh(R0 after logout) -> 401  body={"detail":"Refresh token is invalid."}
[verdict] refresh-after-logout HELD=True (logout==204 & refresh==401)
```

**Verdict**

Defense HELD. CONFIRMS-FIXED: logout's `revoke_by_hash` commits the `revoked_at`
timestamp; `TokenRotator.consume` then sees the row already revoked, so its conditional
`revoke_by_hash` touches 0 rows → `RefreshTokenInvalidError` → 401
(`token_rotator.py:62-63`). The logout→refresh sequence shares the same DB storage state
across separate requests/transactions (proof the revoke is committed, not in-memory).

**Notes / follow-up**

Complements TC-IA-040 (rotation single-use) — together they show both revoke paths
(rotation-consume and explicit logout) durably invalidate a refresh token.
