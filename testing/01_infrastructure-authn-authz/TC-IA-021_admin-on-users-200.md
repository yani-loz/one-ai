# TC-IA-021: Admin token on `GET /users` → 200 (positive control)

| Field | Value |
|---|---|
| **ID** | TC-IA-021 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Positive |
| **Severity if it fails** | Info |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Positive control proving the role gate is not just a deny-all: a real `company_admin`
token on `GET /users` returns 200 with the caller's own-org user list. Anchors TC-IA-020
(without this, a 403 could merely mean the endpoint is broken for everyone).

## Break hypothesis
A violation would be a 403/401/500 for a legitimately-authorized admin, or a 200 whose
body contains users from another org (the latter is a cross-tenant leak — tested
directly in TC-IA-032, but watched here too).

## Preconditions
- Live stack; fresh run-stamped org `authz-<stamp>` onboarded via the platform admin.
- Admin `authz-admin-<stamp>@example.com` logged in for a real `company_admin` token.

## Steps
1. Platform-login; onboard a fresh org.
2. Admin logs in → real admin token.
3. `GET /users` with the admin token; inspect status and the returned roster.

## Expected result
- `GET /users` → **200** with a JSON list. For a freshly-onboarded org the list is exactly
  the one `company_admin` (the onboarding admin), all carrying the caller's `org_id`.

## Harness
Script: `harness/tc_021.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_021.py`

---

## Execution result

- **Run at:** 2026-05-31 08:42 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> `GET /users` with the real admin token returned **200** and a single-element list: the
> onboarding `company_admin`, carrying the org's own `org_id`. The role gate admits the
> admin; the list is scoped to the caller's org.

**Evidence**

```
[setup] onboard_org -> 201
[setup] admin login -> 200
[control] GET /users (admin token) -> 200 [{"id":"c948be62-...","email":"authz-admin-19e7d3298309404@example.com","role":"company_admin","is_active":true,"org_id":"2cd7745a-40e6-4cf3-a270-2e9771da8fc7",...}]
[control] user_count=1; roles=['company_admin']
```

**Verdict**

Defense **held** (positive control). `require_company_admin`
(`dependencies.py:90-100`) admits the `company_admin` principal, and `list_users`
returns 200 with a tenant-scoped roster (the single onboarding admin). Confirms the
gate is a true role filter, not a blanket deny — the necessary counterpart to TC-IA-020.

**Notes / follow-up**

`UserResponse` includes `is_active` as expected. Cross-tenant scoping of this list is
proven separately in TC-IA-032 (admin A's `GET /users` returns only A's users).
