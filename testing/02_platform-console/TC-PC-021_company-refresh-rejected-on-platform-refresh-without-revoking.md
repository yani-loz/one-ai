<!--
  XDOM suite — cross-domain confinement. See ../README.md for legend/tags.
-->

# TC-PC-021: Company refresh token rejected on `POST /platform/refresh` **without revoking it**

| Field | Value |
|---|---|
| **ID** | TC-PC-021 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial (DISCRIMINATING) |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-02-AC3b: a REAL company refresh token presented to `POST /platform/refresh` is
rejected (401, domain/subject_type mismatch) **and is not consumed** — the same token must
still rotate at `POST /auth/refresh` afterwards. A foreign token presented to the wrong
domain must not let a caller deny-of-service (revoke) someone else's session.

## Break hypothesis
If `TokenRotator.consume` revoked the row *before* checking `subject_type`, the company
refresh token would be burned by the failed `/platform/refresh` attempt; the subsequent
`/auth/refresh` would then 401 (token already revoked). The guard's correctness is the
*ordering*: validate subject_type and expiry, raise BEFORE the conditional revoke.

## Preconditions
- Live stack up. Run-stamped fresh company provisioned via `provision_company(c, plat, "xdom")`
  (slug `xdom-<stamp>`, email `admin-xdom-<stamp>@oneai.dev`).
- The company's REAL refresh token (subject_type='user').
- **Step order is load-bearing:** `/platform/refresh` (→401) MUST run before `/auth/refresh`
  (→200); reversed, `/auth/refresh` consumes the token and `/platform/refresh` then 401s for
  the wrong reason.

## Steps
1. `provision_company` → company admin (access, refresh).
2. `POST /platform/refresh` with the company refresh token → expect 401, no revoke.
3. `POST /auth/refresh` with the SAME company refresh token → expect 200 + a NEW pair.

## Expected result
- Step 2: `401 {"detail":"Refresh token is invalid."}`.
- Step 3: `200` with `{access_token, refresh_token, token_type}`, refresh **different** from
  the presented one (rotated) → proves the token was never revoked by step 2.

## Harness
Script: `harness/tc_021.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_021.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The company refresh token was rejected at `/platform/refresh` with 401. The identical token
> then rotated successfully at `/auth/refresh`, returning a new access+refresh pair whose
> refresh value differed from the one presented — proving the failed platform attempt did NOT
> consume/revoke it. The subject_type guard rejects without revoking, exactly as AC3b requires.

**Evidence**

```
== TC-PC-021 — company refresh rejected on /platform/refresh WITHOUT revoking (AC3b) ==
[setup]   provisioned company: xdom-19e8264f7f8815f email admin-xdom-19e8264f7f8815f@oneai.dev
[attack]  POST /platform/refresh (company refresh): 401
          body: {'detail': 'Refresh token is invalid.'}
[proof]   POST /auth/refresh (SAME company refresh): 200
          new pair issued? access? True refresh? True rotated(diff)? True
RESULT: PASS — subject_type guard rejected WITHOUT revoking (token still rotated)
```

**Verdict**

The defense held. `TokenRotator.consume`
(`backend/app/identity/services/token_rotator.py:55-64`) checks `stored.subject_type !=
expected_subject_type` and raises `RefreshTokenInvalidError` **before** the conditional
`revoke_by_hash` — so a foreign-domain token is rejected without being consumed. PlatformAuthService
calls `consume(..., 'platform_admin')` (`platform_auth_service.py:107`), so the company token
(subject_type='user') mismatches and 401s. The token survives, proven live by the subsequent
`/auth/refresh` rotation. CONFIRMS-FIXED (PC-02-AC3b) — no DoS-on-foreign-session primitive.

**Notes / follow-up**
Counterpart to TC-PC-020 on the refresh surface. The reject-without-revoke property is what
prevents one company from killing another caller's session by presenting their token at the
wrong endpoint.
