# TC-IA-030: Cross-tenant PATCH /users/{id} resolves to 404 without mutating the target

| Field | Value |
|---|---|
| **ID** | TC-IA-030 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Cross-tenant isolation (hardest rule) |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A `company_admin` of org A must not be able to mutate a user belonging to org B. The
contract: `PATCH /users/{B_user_id}` → **404 "User not found."** (never 200, never a
403-with-info, never a partial mutation of B's row). Verifies the app-layer
`UserRepository.get_in_org(user_id, org_id)` tenant scope — the only *active* isolation
control (RLS is inert).

## Break hypothesis
If the service resolved the target user by id alone (or filtered loosely), admin A's
PATCH would either succeed (200, mutating B's `full_name`/`is_active`/`role`) or return a
distinguishable error that leaks B's existence. The bet: a missed `WHERE org_id` would
let A rename/deactivate B's admin.

## Preconditions
- Live stack (`docker compose up`), API at `http://localhost:8000`.
- Run-stamped namespace, suite code `tenant`: fresh orgs `tenant-a-<stamp>` and
  `tenant-b-<stamp>`, admins `tenant-a-admin-<stamp>@tenant.oneai` /
  `tenant-b-admin-<stamp>@tenant.oneai`. Onboarded via the demo platform admin. Demo org
  never touched.

## Steps
1. Platform-login; onboard org A and org B (each with a `company_admin`).
2. Log in as admin A.
3. Admin A sends `PATCH /users/{B_admin_id}` with body `{"full_name":"PWNED-BY-A","is_active":false}`.
4. Log in as admin B (proves the account is still active/unmutated); read `/auth/me` and
   `GET /users` in B to confirm `full_name`, `role`, `is_active` are unchanged.

## Expected result
- PATCH → `404` with body `{"detail":"User not found."}`.
- B's admin: login still `200`; `full_name` still `Org Admin`; `role` still
  `company_admin`; `is_active` still `true`. No field mutated.

## Harness
Script: `harness/tc_030.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_030.py`

---

## Execution result

- **Run at:** 2026-05-31 11:39 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Admin A's cross-tenant PATCH returned `404 {"detail":"User not found."}`. B's admin was
> entirely unmutated: B login still succeeded (200), and `/auth/me` + `GET /users` showed
> `full_name: "Org Admin"`, `role: "company_admin"`, `is_active: true` — the attacker's
> `"PWNED-BY-A"` / `is_active:false` had no effect.

**Evidence**

```
ONBOARD A: 201 | ONBOARD B: 201
A org: 131eb943-c461-491f-ab70-dd687875e21f
B org: 4fb42267-f382-40ac-b816-d1d78b6ed52c | B admin user id: 2b351bc4-a640-4888-a514-a77a1489464a | name: Org Admin | role: company_admin
CROSS-TENANT PATCH status: 404
CROSS-TENANT PATCH body: {"detail":"User not found."}
B LOGIN AFTER ATTACK status: 200 (200 proves B admin still active & unmutated)
B /auth/me body: {"id":"2b351bc4-a640-4888-a514-a77a1489464a","email":"tenant-b-admin-19e7d2fc50e2349@tenant.oneai","full_name":"Org Admin","role":"company_admin","org_id":"4fb42267-f382-40ac-b816-d1d78b6ed52c","org_name":"TENANT-B 19e7d2fc50e2349"}
B /users body: [{"id":"2b351bc4-a640-4888-a514-a77a1489464a","email":"tenant-b-admin-19e7d2fc50e2349@tenant.oneai","full_name":"Org Admin","role":"company_admin","is_active":true,"org_id":"4fb42267-f382-40ac-b816-d1d78b6ed52c","created_at":"2026-05-31T08:39:03.895942Z"}]
```

**Verdict**

Defense **held**. The cross-tenant PATCH resolves to 404 via `UserService.update_user` →
`UserRepository.get_in_org(user_id, org_id)` returning `None`
(`backend/app/identity/services/user_service.py:93-95`,
`backend/app/identity/repositories/user_repository.py:58-67`); `org_id` is taken from the
verified principal (`user_routes.py:53-61`), never the path. No mutation reached B's row.
This **confirms** the audit's tenant-scope claim holds dynamically (the audit's own
Limitations section flagged the absence of exactly this dynamic test).

**Notes / follow-up**

This holds only because of the app-layer `org_id` filter; with a forged token the
isolation collapses entirely — see TC-IA-035. The 404 (not 403) is correct: it does not
reveal that the id exists in another org.
