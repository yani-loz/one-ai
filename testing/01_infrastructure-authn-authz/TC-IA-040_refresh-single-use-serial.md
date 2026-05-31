<!--
  Test-case: TC-IA-040. See ../README.md for legend, tags, severity scale.
-->

# TC-IA-040: Refresh rotation is single-use (serial reuse of the old token → 401)

| Field | Value |
|---|---|
| **ID** | TC-IA-040 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Token lifecycle |
| **Type** | Negative |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Refresh-token rotation is single-use: once a refresh token is consumed (rotated into a
new pair), re-presenting the SAME old token must be rejected with `401
RefreshTokenInvalid`. This is the serial half of AUD-01's single-use invariant
(`token_rotator.py:54-64` now revokes atomically via `revoke_by_hash`).

## Break hypothesis
If `consume()` did not durably revoke the old token (or revoked only in memory), the old
refresh token would still rotate a second time → a second valid `200` pair from one
token. The attacker's bet: re-presenting the old token yields `200` with a fresh pair.

## Preconditions
- Live stack at `http://localhost:8000`; platform admin seed available.
- Fresh run-stamped org provisioned via `POST /platform/orgs` (namespace `token-<stamp>`).
- A company user (the org admin) logged in to obtain an initial refresh token.

## Steps
1. Platform-login; onboard a fresh org `token-<stamp>` with admin `admin-<stamp>@token.example.com`.
2. `POST /auth/login` as that admin → capture `refresh_token` R0.
3. `POST /auth/refresh` with R0 → expect `200` + new pair (R1). R0 is now consumed.
4. `POST /auth/refresh` with R0 again (the OLD, consumed token) → expect `401`.

## Expected result
- Step 3: `200`, body has a new `access_token` + `refresh_token` (R1 ≠ R0).
- Step 4: `401` with `{"detail":"Refresh token is invalid."}` (RefreshTokenInvalidError).

## Harness
Script: `harness/tc_040.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_040.py`

---

## Execution result

- **Run at:** 2026-05-31 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> First refresh of R0 returned `200` with a brand-new pair (R1 ≠ R0). Re-presenting the
> OLD, now-consumed R0 returned `401 {"detail":"Refresh token is invalid."}`. The
> single-use rotation invariant holds under serial reuse — AUD-01's atomic
> `revoke_by_hash` consume guard is durable.

**Evidence**

```
[setup] namespace=token-19e7d3001643a77 slug=token-19e7d3001643a77 admin=admin-19e7d3001643a77@token.example.com
[setup] onboard_org -> 201
[step2] login -> 200
[step2] R0(prefix)=NoXNbD1y8ShC...
[step3] refresh(R0) -> 200  body_keys=['access_token', 'refresh_token', 'token_type']
[step3] R1(prefix)=s47LOJNxUZAX...  R1!=R0 -> True
[step4] refresh(OLD R0 again) -> 401  body={"detail":"Refresh token is invalid."}
[verdict] single-use HELD=True (step3==200 & R1!=R0 & step4==401)
```

**Verdict**

Defense HELD. CONFIRMS-FIXED for AUD-01: `TokenRotator.consume`
(`backend/app/identity/services/token_rotator.py:62-63`) now revokes the presented token
via the conditional `RefreshTokenRepository.revoke_by_hash`
(`backend/app/identity/repositories/refresh_token_repository.py:50-55`,
`WHERE token_hash=:hash AND revoked_at IS NULL`) and treats `rowcount == 0` as
`RefreshTokenInvalidError` → 401. Serial reuse of a consumed token is rejected. (The
concurrency half of AUD-01 — N simultaneous refreshes → exactly 1 success — is the
separate TC-IA-053 case.)

**Notes / follow-up**

Serial reuse only. The audit's concurrency race (two simultaneous presentations) is
covered by TC-IA-053; this case proves the durable, committed revoke.
