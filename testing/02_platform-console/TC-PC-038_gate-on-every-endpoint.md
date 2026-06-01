<!-- PAZ suite — Platform token-validation matrix (401 not 403/500). -->

# TC-PC-038: Gate on every platform endpoint — `GET`/`POST /platform/orgs` → 401

| Field | Value |
|---|---|
| **ID** | TC-PC-038 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PAZ — Platform token-validation matrix |
| **Type** | Negative |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove the `get_current_platform_admin` gate is wired on **every** protected platform endpoint, not
only `/platform/me`: both `GET /platform/orgs` (metadata list) and `POST /platform/orgs` (onboard)
reject a missing bearer and an `alg=none` token with **401**.

## Break hypothesis
A copy-paste slip could leave one route's `Depends(get_current_platform_admin)` off, so `GET orgs`
(tenant-metadata leak) or `POST orgs` (unauthenticated org creation — a fail-open write) would
succeed without a token. The bet: one of the four probes returns non-401 (200/201/422/500) — and
for `POST`, an org row is created.

## Preconditions
- Live stack up. `alg=none` token from `forge_platform_token(alg='none')`. The POST body is a
  **valid** onboard payload (run-stamped `paz-gate-<stamp>` slug + email) so a rejection is
  unambiguously *auth* (401), never a *validation* 422. Demo admin used only to... (not used here;
  probes are unauthenticated by design). No demo mutation.

## Steps
1. `GET /platform/orgs` with no bearer → expect 401.
2. `GET /platform/orgs` with `alg=none` token → expect 401.
3. `POST /platform/orgs` (valid body) with no bearer → expect 401.
4. `POST /platform/orgs` (valid body) with `alg=none` token → expect 401.
5. psql ground-truth: assert **zero** orgs exist with a `paz-gate-%` slug (no fail-open write).

## Expected result
- All four → **401** (`Missing bearer token.` / `Access token is invalid.`). Never 200/201/422/500.
- `SELECT count(*) FROM organizations WHERE slug LIKE 'paz-gate-%'` → **0**.

## Harness
Script: `harness/tc_038.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_038.py | docker compose exec -T backend python -`
psql: `docker compose exec -T db psql -U oneai -d oneai -c "SELECT count(*) FROM organizations WHERE slug LIKE 'paz-gate-%';"`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> All four probes returned **401**. Because the POST body was valid, the 401 (not 422) proves the
> auth gate runs *before* body handling. psql confirmed **no** `paz-gate-%` org was created — the
> rejected onboard POSTs were fully fail-closed (no orphan write).

**Evidence**

```
GET /platform/orgs NO-BEARER -> 401 {"detail":"Missing bearer token."}
GET /platform/orgs ALG=NONE -> 401 {"detail":"Access token is invalid."}
POST /platform/orgs NO-BEARER -> 401 {"detail":"Missing bearer token."}
POST /platform/orgs ALG=NONE -> 401 {"detail":"Access token is invalid."}
assert_all_401: PASS {'GET orgs no-bearer': 401, 'GET orgs alg=none': 401, 'POST orgs no-bearer': 401, 'POST orgs alg=none': 401}
```

```
-- psql ground truth (no fail-open write)
 paz_gate_orgs
---------------
             0
(1 row)
```

**Verdict**
Defense held on the whole surface. Code path: both `list_organizations`
(`backend/app/identity/routes/platform_routes.py:115-121`) and `onboard_organization`
(`platform_routes.py:101-112`) declare `Depends(get_current_platform_admin)`; missing bearer →
`TokenInvalidError` (`dependencies.py:114-115`), `alg=none` → decoder `InvalidTokenError`
(`tokens.py:87-88`) → both 401. The gate is not only on `/me`. POST rejection precedes any DB
write, so onboarding is fail-closed.

**Notes / follow-up**
Spot-check satisfied: the token gate is uniform across `/platform/me`, `GET /platform/orgs`, and
`POST /platform/orgs`. Onboarding-contract depth (409s, rollback, bounds) is the ONB suite's remit.
