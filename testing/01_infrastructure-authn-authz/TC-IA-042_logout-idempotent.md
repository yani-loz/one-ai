<!--
  Test-case: TC-IA-042. See ../README.md for legend, tags, severity scale.
-->

# TC-IA-042: Logout is idempotent (same refresh token twice → 204 / 204)

| Field | Value |
|---|---|
| **ID** | TC-IA-042 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Token lifecycle |
| **Type** | Positive |
| **Severity if it fails** | Info |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
`POST /auth/logout` revokes the presented refresh token and is idempotent: logging out
the SAME token a second time returns `204` (no error). The revoke is a conditional
`UPDATE ... WHERE revoked_at IS NULL` (`refresh_token_repository.py:50-55`); a second
call touches 0 rows and `revoke()` treats that as a no-op (`token_rotator.py:66-68`).

## Break hypothesis
If logout raised on an already-revoked/unknown token (e.g. asserting rowcount > 0), the
second logout would return `4xx/5xx` instead of `204`. The bet: second logout ≠ 204.

## Preconditions
- Live stack; fresh run-stamped org `token-<stamp>` + admin logged in.

## Steps
1. Onboard a fresh org; login as admin → capture refresh R0.
2. `POST /auth/logout` with R0 → expect `204`.
3. `POST /auth/logout` with R0 again (already revoked) → expect `204`.
4. (Control) `POST /auth/logout` with a never-issued random token → expect `204` (unknown
   token is a no-op, not an error).

## Expected result
- All three logout calls return `204 No Content` with an empty body.

## Harness
Script: `harness/tc_042.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_042.py`

---

## Execution result

- **Run at:** 2026-05-31 (local)
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> Logging out R0 returned `204` (empty body). Logging out the SAME already-revoked R0
> again returned `204`. Logging out a never-issued random token also returned `204`. All
> three are no-error no-ops — logout is idempotent for both already-revoked and unknown
> tokens.

**Evidence**

```
[setup] namespace=token-19e7d32eb397c44 slug=token-19e7d32eb397c44 admin=admin-19e7d32eb397c44@token.example.com
[setup] onboard_org -> 201
[setup] login -> 200  R0(prefix)=9CgV7Pv3uZ3H...
[step2] logout(R0) -> 204  body=''
[step3] logout(R0 again, already revoked) -> 204  body=''
[step4] logout(unknown random token) -> 204  body=''
[verdict] logout-idempotent HELD=True (all three == 204)
```

**Verdict**

Defense HELD (positive contract). `TokenRotator.revoke` (`token_rotator.py:66-68`) calls
`revoke_by_hash`, whose conditional `WHERE ... revoked_at IS NULL`
(`refresh_token_repository.py:50-55`) touches 0 rows on an already-revoked or unknown
token and returns `rowcount=0` without raising. The route returns `204` regardless. No
existence leak (unknown vs known token are indistinguishable: both 204).

**Notes / follow-up**

None.
