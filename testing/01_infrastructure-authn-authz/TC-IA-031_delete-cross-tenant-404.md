# TC-IA-031: Cross-tenant DELETE /users/{id} resolves to 404 without deactivating the target

| Field | Value |
|---|---|
| **ID** | TC-IA-031 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Cross-tenant isolation (hardest rule) |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A `company_admin` of org A must not be able to deactivate (soft-delete) a user belonging
to org B. Contract: `DELETE /users/{B_user_id}` → **404**, and B's user remains active.
Verifies `UserService.deactivate_user` → `get_in_org` tenant scope.

## Break hypothesis
If `deactivate_user` resolved the target by id alone, admin A's DELETE would flip B's
`is_active=False` (soft delete), locking a B user — or worse, B's last admin — out, an
availability + isolation breach. The bet: a missed `WHERE org_id` lets A deactivate B.

## Preconditions
- Live stack; run-stamped suite `tenant` orgs A/B with admins, plus a plain **member** in
  B (`tenant-b-mem-<stamp>@tenant.oneai`) so the deleted target is unambiguously a
  non-last-admin (no last-admin guard interference). Demo org untouched.

## Steps
1. Onboard orgs A and B; add a member to B.
2. Log in as admin A.
3. Admin A `DELETE /users/{B_member_id}` and `DELETE /users/{B_admin_id}`.
4. Confirm both B users still active: B member login `200`, B admin login `200`, and
   `GET /users` in B shows `is_active:true` for both.

## Expected result
- Both DELETEs → `404 {"detail":"User not found."}`.
- B member + B admin: login `200`; `is_active` still `true`.

## Harness
Script: `harness/tc_031.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_031.py`

---

## Execution result

- **Run at:** 2026-05-31 11:41 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Both cross-tenant DELETEs returned `404 {"detail":"User not found."}`. B's member and B's
> admin both still logged in (200) afterward, and B's `GET /users` showed `is_active:true`
> for both rows — no soft-delete reached B.

**Evidence**

```
ONBOARD A: 201 | ONBOARD B: 201
B member created: 201 id: c9203182-0650-4089-8731-3e2c3371ce1c
CROSS-TENANT DELETE (B member) status: 404 body: {"detail":"User not found."}
CROSS-TENANT DELETE (B admin) status: 404 body: {"detail":"User not found."}
B MEMBER LOGIN AFTER DELETE status: 200 (200 => still active)
B ADMIN LOGIN AFTER DELETE status: 200 (200 => still active)
B /users (is_active flags): [{"id":"c9203182-0650-4089-8731-3e2c3371ce1c","email":"tenant-b-mem-19e7d318ddb70f4@tenant.oneai","full_name":"B Member","role":"member","is_active":true,"org_id":"ca129d18-47e5-40b3-9cc9-62bc61b741e1","created_at":"2026-05-31T08:41:01.377868Z"},{"id":"0232410a-11bf-4938-8d4c-d9fc1e3a9b87","email":"tenant-b-admin-19e7d318ddb70f4@tenant.oneai","full_name":"Org Admin","role":"company_admin","is_active":true,"org_id":"ca129d18-47e5-40b3-9cc9-62bc61b741e1","created_at":"2026-05-31T08:41:00.823164Z"}]
```

**Verdict**

Defense **held**. `DELETE /users/{id}` → `UserService.deactivate_user`
(`user_service.py:109-121`) calls `get_in_org(user_id, org_id)`; a cross-org target
returns `None` → `UserNotFoundError` → 404 before any `is_active=False` write. Both B
users remained active (login 200, `is_active:true`). Confirms the audit's tenant-scope
fix holds dynamically for the delete path.

**Notes / follow-up**

The 404 precedes the last-admin guard, so a cross-tenant delete of B's last admin is also
a clean 404 (not a 409) — correct: it does not even acknowledge B's user exists. Isolation
still rests solely on the app-layer filter (cf. TC-IA-035).
