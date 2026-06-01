# TC-PC-040: Onboard a fresh org — positive response-shape contract

| Field | Value |
|---|---|
| **ID** | TC-PC-040 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | ONB — Onboarding contracts + input validation/fuzz |
| **Type** | Positive |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`POST /platform/orgs` with a valid payload returns **201** and the exact onboarding contract:
`organization` carries EXACTLY `{id,name,slug,status,user_count,created_at}` with `user_count=1`,
and `admin` is a `UserResponse` (`role=company_admin`, `is_active=true`, `org_id` = the new org)
with NO `password_hash`.

## Break hypothesis
The response leaks an extra field (e.g. `password_hash` on the admin, or a tenant-content field on
the org), `user_count` is wrong/absent, or `created_at` is missing — any of which violates the
metadata-only / no-secret-exposure contract (`platform_schemas.py`).

## Preconditions
- Live stack healthy (`/health` → `database: reachable`).
- Demo platform admin `super@ethera.ai` used to mint the token (never mutated).
- Run-stamped namespace: slug `onb40-<stamp>`, email `onb40-<stamp>@oneai.dev`.

## Steps
1. Platform-login the demo admin → access token.
2. `onboard_org` a fresh run-stamped org.
3. Assert 201; introspect `organization` and `admin` key sets, `user_count`, `created_at`,
   `role`, `is_active`, `org_id`, and absence of `password_hash`.

## Expected result
201; `organization` keys == `{created_at,id,name,slug,status,user_count}`, `user_count==1`,
`created_at` present; `admin` is a UserResponse, `role==company_admin`, `is_active==true`,
`org_id==organization.id`, `password_hash` absent.

## Harness
Script: `harness/tc_040.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_040.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 08:51 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> 201 with the exact 6-field org metadata (`user_count=1`, `created_at` present) and a clean
> `UserResponse` admin (`role=company_admin`, `is_active=true`, `org_id` matches the new org,
> NO `password_hash`).

**Evidence**

```
onboard status: 201
body: {'organization': {'id': '39e49f08-2140-452a-a374-24fb6b1ab6d7', 'name': 'Org onb40-19e8261d51df7aa', 'slug': 'onb40-19e8261d51df7aa', 'status': 'active', 'user_count': 1, 'created_at': '2026-06-01T08:51:50.178652Z'}, 'admin': {'id': '40e79383-956d-45f0-80a3-61b12c282b58', 'email': 'onb40-19e8261d51df7aa@oneai.dev', 'full_name': 'ONB Forty Admin', 'role': 'company_admin', 'is_active': True, 'org_id': '39e49f08-2140-452a-a374-24fb6b1ab6d7', 'created_at': '2026-06-01T08:51:50.178652Z'}}
org keys (sorted): ['created_at', 'id', 'name', 'slug', 'status', 'user_count']
org user_count: 1
org created_at present: True
admin keys (sorted): ['created_at', 'email', 'full_name', 'id', 'is_active', 'org_id', 'role']
admin role: company_admin
admin password_hash present: False
admin is_active: True
admin org_id == org id: True
```

**Verdict**

Defense held. The serialized contract matches `OrganizationOnboardedResponse`
(`platform_schemas.py:62-67`) exactly: `OrganizationResponse` exposes only the 6 metadata fields
(`platform_schemas.py:49-60`) and `UserResponse` omits `password_hash` (`user_schemas.py:102-114`).
`user_count=1` is hard-set by `onboard_organization` (`platform_auth_service.py:188`). Confirms the
PC-02 / PR-1 response-shape fixes hold live.

**Notes / follow-up**

Baseline positive control for the ONB suite; the adversarial cases (041-048) build on this shape.
