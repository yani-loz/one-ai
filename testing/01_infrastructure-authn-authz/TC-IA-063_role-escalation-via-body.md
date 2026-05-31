# TC-IA-063: `role=platform_admin` via body → 422 (no escalation)

| Field | Value |
|---|---|
| **ID** | TC-IA-063 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Input validation on auth surfaces (IV) |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Verify the `POST /users` surface cannot mint a privileged or arbitrary role. The
`UserRole` enum is `{company_admin, member}` only; a request body carrying
`role='platform_admin'`, `role='superadmin'`, or `role=''` must each be rejected with
`422` (enum validation), creating no user with that role.

## Break hypothesis
If `role` were a free `str` (not the `UserRole` enum), a company_admin could create a
user with `role='platform_admin'` and cross the company↔platform auth boundary, or
inject an arbitrary role string that downstream authZ mis-handles. Any non-enum role
accepted (a `201` with that stored role) is a **High** privilege-escalation defect.

## Preconditions
- Live stack; namespace `iv-063-*`; fresh org + admin.

## Steps
1. Onboard fresh org + admin; company-login.
2. `POST /users` three times with `role` ∈ {`platform_admin`, `superadmin`, `''`}.
3. Control: `POST /users` with `role='member'` to prove the surface is otherwise live.

## Expected result
`422` (enum error, "Input should be 'company_admin' or 'member'") for all three hostile
roles; `201` with `role='member'` for the control. No created user carries a non-enum role.

## Harness
Script: `harness/tc_063.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_063.py`

---

## Execution result

- **Run at:** 2026-05-31 12:02 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> All three hostile roles were rejected with `422` enum errors; no privileged user was
> created. The `member` control succeeded (`201`, stored `role=member`).

**Evidence**

```
[onboard] status=201
[role='platform_admin'] status=422 body={"detail":[{"type":"enum","loc":["body","role"],"msg":"Input should be 'company_admin' or 'member'","input":"platform_admin","ctx":{"expected":"'company_admin' or 'member'"}}]}
[role='superadmin'] status=422 body={"detail":[{"type":"enum","loc":["body","role"],"msg":"Input should be 'company_admin' or 'member'","input":"superadmin","ctx":{"expected":"'company_admin' or 'member'"}}]}
[role='<empty>'] status=422 body={"detail":[{"type":"enum","loc":["body","role"],"msg":"Input should be 'company_admin' or 'member'","input":"","ctx":{"expected":"'company_admin' or 'member'"}}]}
[control role='member'] status=201 stored_role=member
```

**Verdict**

Defense **held**. `UserCreateRequest.role: UserRole` (`user_schemas.py:50`) constrains
the field to the two-value enum (`enums.py:17-21`), so no request can introduce a
`platform_admin` or arbitrary role through this surface — the company↔platform domain
split cannot be crossed via the create body. Confirms the schema's stated invariant
("Roles are constrained to UserRole, so a request can never create a platform admin
through this surface"). No High-severity escalation; this is a passing adversarial control.

**Notes / follow-up**

Token-side role escalation (forging an `org_id`/`role` JWT) is a separate, documented
capability — covered by the authZ forgery cases (TC-IA-035/036), not this input surface.
