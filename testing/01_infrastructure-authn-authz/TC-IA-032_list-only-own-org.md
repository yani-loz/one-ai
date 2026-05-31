# TC-IA-032: GET /users returns only the caller's org users (zero cross-tenant emails)

| Field | Value |
|---|---|
| **ID** | TC-IA-032 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Cross-tenant isolation (hardest rule) |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`GET /users` by admin A must return **only** org A's users — never any org B email or
row. Verifies `UserRepository.list_by_org(org_id)` is org-scoped and the route derives
`org_id` from the verified principal.

## Break hypothesis
If `list_users` queried all users (missing `WHERE org_id`), admin A's list would include
org B's admin/member emails — a direct cross-tenant membership disclosure. The bet: A sees
B's `tenant-b-*@tenant.oneai` addresses.

## Preconditions
- Live stack; suite `tenant` orgs A/B, each with an admin **and** a member
  (`tenant-a-mem-<stamp>`, `tenant-b-mem-<stamp>`) so each org's list has 2 rows — a
  richer surface for a leak. Demo org untouched.

## Steps
1. Onboard A and B; log in as each admin; add a member to each org.
2. Admin A `GET /users`.
3. Assert A's list emails ⊆ {A admin, A member}, that no B email appears, and that the
   only `org_id` in the rows is A's.

## Expected result
- A's list = exactly `[tenant-a-admin, tenant-a-mem]`; intersection with
  {tenant-b-admin, tenant-b-mem} is empty; distinct `org_id` set = `{A}`.

## Harness
Script: `harness/tc_032.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_032.py`

---

## Execution result

- **Run at:** 2026-05-31 11:41 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Admin A's `GET /users` returned exactly A's two emails. Zero B emails leaked; the only
> `org_id` present was A's.

**Evidence**

```
ONBOARD A: 201 | ONBOARD B: 201
A GET /users status: 200
A list emails: ['tenant-a-admin-19e7d319a8a248a@tenant.oneai', 'tenant-a-mem-19e7d319a8a248a@tenant.oneai']
A list distinct org_ids: ['40d90627-3f7e-43ba-8b6c-569d193b76ab']
B emails that MUST NOT appear: ['tenant-b-admin-19e7d319a8a248a@tenant.oneai', 'tenant-b-mem-19e7d319a8a248a@tenant.oneai']
LEAKED B emails in A's list: [] (empty == pass)
A list contains B org_id? False (False == pass)
A list org_ids == {A only}? True
```

**Verdict**

Defense **held**. `GET /users` → `UserService.list_users(principal.org_id)` →
`UserRepository.list_by_org(org_id)` filters `WHERE org_id = :org`
(`user_repository.py:69-74`); `principal.org_id` is the verified JWT claim
(`user_routes.py:34-40`). A's list contained only A's rows, no B email, single org_id.
Confirms the org-scoped-list fix holds dynamically.

**Notes / follow-up**

Same caveat as the suite: this is the app-layer filter, the only active control. A forged
token bearing B's `org_id` reads B's list (TC-IA-035) — the list scope is correct but has
no DB backstop.
