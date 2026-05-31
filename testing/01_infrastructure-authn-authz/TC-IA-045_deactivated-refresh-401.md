<!--
  Test-case: TC-IA-045. See ../README.md for legend, tags, severity scale.
-->

# TC-IA-045: Deactivated user's refresh token → 401

| Field | Value |
|---|---|
| **ID** | TC-IA-045 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Token lifecycle |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Deactivating a user kills their refresh-token rotation: after admin `DELETE /users/{id}`,
the deactivated user's refresh token must fail with `401`. `consume()` revokes the
presented token, then `AuthService.refresh` resolves the subject via `get_by_subject_id`
and rejects `not user.is_active` → InvalidCredentialsError (401) (`auth_service.py:87-89`).

## Break hypothesis
If `refresh` did not re-check `is_active`, a deactivated user could keep rotating fresh
access tokens indefinitely from a pre-deactivation refresh token — full session survival.
The bet: refresh after deactivation yields `200` with a new pair.

## Preconditions
- Live stack; fresh run-stamped org `token-<stamp>` with admin + a member
  `victim-<stamp>@token.test` (member is the deactivation target, to avoid the
  last-admin guard).

## Steps
1. Onboard org; admin logs in; admin creates member `victim-<stamp>@token.test`.
2. Member logs in → capture the member's refresh token R0.
3. Admin `DELETE /users/{member_id}` → expect `204`.
4. `POST /auth/refresh` with R0 → expect `401`.
5. (Side-check) Re-present R0 again → still `401` (it was consumed/revoked by step 4's
   `consume`, so the failure mode is now "revoked" rather than "inactive" — both 401).

## Expected result
- Step 3: `204`.
- Step 4: `401` with `{"detail":"Invalid email or password."}` (InvalidCredentialsError —
  the rotation consumed the token, then the inactive check fired).

## Harness
Script: `harness/tc_045.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_045.py`

---

## Execution result

- **Run at:** 2026-05-31 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> After admin `DELETE /users/{member}` (`204`), the deactivated member's refresh token R0
> returned `401 {"detail":"Invalid email or password."}` on `/auth/refresh`. A second
> presentation of R0 also returned `401` (now because the first `consume` already revoked
> it). A deactivated user cannot rotate a fresh access token from a pre-deactivation
> refresh token.

**Evidence**

```
[setup] namespace=token-19e7d333e636c05 ... victim=victim-19e7d333e636c05@token.example.com
[setup] onboard_org -> 201
[step1] create member -> 201
[step2] victim login -> 200  R0(prefix)=FhpjYnBRyQPW...
[step3] admin DELETE /users/{member} -> 204  body=''
[step4] refresh(victim R0, now deactivated) -> 401  body={"detail":"Invalid email or password."}
[step5] refresh(victim R0 again) -> 401  body={"detail":"Invalid email or password."}
[verdict] deactivated-refresh HELD=True (delete==204 & refresh==401 & re-refresh==401)
```

**Verdict**

Defense HELD. CONFIRMS-FIXED: `AuthService.refresh` (`auth_service.py:86-89`) runs
`consume()` (which revokes the presented token) then resolves the subject via
`get_by_subject_id` and raises `InvalidCredentialsError` (→401) on `not user.is_active`.
The `401` detail message is the generic `"Invalid email or password."` (InvalidCredentials
path), confirming the inactive-account branch fired rather than a token-format rejection.
A deactivated account's session is severed at the refresh boundary — it cannot mint new
access tokens.

**Notes / follow-up**

Note the consume-then-check ordering: even though the inactive check raises, the token was
already revoked by `consume`, so step 5 reuse is independently rejected. Both are 401.
