<!--
  XDOM suite — cross-domain confinement. See ../README.md for legend/tags.
-->

# TC-PC-023: Real COMPANY admin token rejected on `/platform/orgs` (GET + POST)

| Field | Value |
|---|---|
| **ID** | TC-PC-023 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A REAL company-admin access token (aud='company') must be rejected (401) on the platform
governance surface — both `GET /platform/orgs` (fleet listing) and `POST /platform/orgs`
(onboard). The company side cannot read other customers' metadata nor create orgs.

## Break hypothesis
If `get_current_platform_admin` did not pin the audience to `platform`, a company token would
decode and pass; `GET /platform/orgs` would leak the full org fleet to a single tenant's admin,
and `POST /platform/orgs` would let a tenant admin onboard arbitrary orgs. The audience guard
is what blocks the company-aud token here (its role claim — company_admin — is irrelevant; the
platform gate doesn't role-check, it audience-checks).

## Preconditions
- Live stack up. Run-stamped fresh company provisioned via `provision_company(c, plat, "xdom")`.
- The POST attempt uses a run-stamped slug `xdom-shouldnotexist-<stamp>` so that even if it
  *were* (wrongly) accepted, the artifact is namespaced and detectable.

## Steps
1. `provision_company` → company admin access token.
2. `GET /platform/orgs` with the company token.
3. `POST /platform/orgs` (onboard a run-stamped org) with the company token.

## Expected result
- Both → `401` (audience mismatch). No org is created by step 3 (verifiable by the absence of
  the `xdom-shouldnotexist-*` slug in the DB).

## Harness
Script: `harness/tc_023.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_023.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The real company-admin token was rejected with 401 on both `GET` and `POST /platform/orgs`.
> The company side cannot reach the platform governance endpoints in either read or write.

**Evidence**

```
== TC-PC-023 — real COMPANY admin token rejected on /platform/orgs (GET + POST) ==
[setup]   provisioned company: xdom-19e82650337bd55 admin admin-xdom-19e82650337bd55@oneai.dev
[attack1] GET /platform/orgs (company admin token): 401
          body: {'detail': 'Access token is invalid.'}
[attack2] POST /platform/orgs (company admin token): 401
          body: {'detail': 'Access token is invalid.'}
RESULT: PASS — company side cannot reach platform endpoints (both 401)
```

**Verdict**

Confinement holds. Both `GET` and `POST /platform/orgs` are gated by
`get_current_platform_admin` (`backend/app/identity/routes/platform_routes.py:108,117`), which
calls `decode_access_token(..., PLATFORM_AUDIENCE)` (`dependencies.py:116`) — a company-aud
token fails the audience check and 401s before any handler runs. The `POST` 401 fires before
the request body is even processed, so no org is created. CONFIRMS-FIXED of the platform/company
separation invariant. (Like TC-PC-022, this is confinement corroboration; the discriminating
"audience is sole load-bearing" claim belongs to TC-PC-020/026.)

**Notes / follow-up**
Pairs with TC-PC-022 (platform token rejected on company endpoints) to complete the mutual seal.
