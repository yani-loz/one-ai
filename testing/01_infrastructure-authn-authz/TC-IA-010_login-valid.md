<!--
  TC-IA-010 — company login happy path. Authored top-half first, Execution result
  written back after running the harness against the live stack.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-IA-010: Company login with correct credentials returns a token pair + user view

| Field | Value |
|---|---|
| **ID** | TC-IA-010 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authentication / login (AUTHN) |
| **Type** | Positive |
| **Severity if it fails** | Info |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Verify the `/auth/login` contract for a valid company user: HTTP 200 with a body that
carries `access_token`, `refresh_token`, `token_type="bearer"`, and a populated `user`
view exposing exactly `{id, email, full_name, role, org_id, org_name}` (no secrets).

## Break hypothesis
A positive test, but a violation would look like: a missing/empty token, `token_type`
other than `bearer`, an absent `user` object on login (it must be present only on login),
a wrong `org_id`/`org_name` (cross-org bleed), or any `password`/`password_hash`/token-hash
field leaking into the response.

## Preconditions
Live stack (`docker compose up`). The harness onboards a FRESH run-stamped org via the
platform admin (`super@ethera.ai`) using `stamp()`-prefixed slug + admin email, then logs
that admin in. The demo org is never touched. Namespace prefix: `authn010-<stamp>`.

## Steps
1. Platform-login as the demo platform admin to get a platform token.
2. Onboard a fresh org `authn010-<stamp>` with admin `admin-authn010-<stamp>@oneai.dev`.
3. POST `/auth/login` with that admin's correct email + `DEFAULT_PW`.
4. Inspect status, the full JSON body, and the set of `user` keys.

## Expected result
- Status `200`.
- Body has non-empty `access_token` and `refresh_token` (distinct strings).
- `token_type == "bearer"`.
- `user` is present with keys exactly `{id, email, full_name, role, org_id, org_name}`,
  `email` == the onboarded admin email, `role == "company_admin"`, `org_id`/`org_name`
  match the onboarded org.
- No `password`, `password_hash`, or hash-like field anywhere in the body.

## Harness
Script: `harness/tc_010.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_010.py`

---

## Execution result

- **Run at:** 2026-05-31 11:42 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> Login returned 200 with both tokens (distinct), `token_type="bearer"`, and a `user`
> view whose key-set is exactly `{id, email, full_name, role, org_id, org_name}`. `email`
> and `org_name` match the onboarded org/admin; `role` is `company_admin`. No credential
> or hash field appears in the body. The contract holds.

**Evidence**

```
== onboard == 201 org_id=c424881b-e2d7-4c11-b310-b943da2bdb48 org_name=authn010 co-19e7d2eefdc5c59 authn010-19e7d2eefdc5c59
== POST /auth/login == 200
body keys: ['access_token', 'refresh_token', 'token_type', 'user']
access_token present: True (len=385)  refresh_token present: True (len=64)
tokens distinct: True
token_type: bearer
user keys: ['email', 'full_name', 'id', 'org_id', 'org_name', 'role']
user.email: admin-authn010-19e7d2eefdc5c59@oneai.dev
user.role: company_admin
user.org_name: authn010 co-19e7d2eefdc5c59
secret-field scan (password/hash) in body: NONE FOUND
RESULT: PASS
```

**Verdict**

Defense held. The login happy path satisfies the `TokenPairResponse` contract
(`auth_schemas.py:53-63`, served by `auth_routes.py:31-36` / `auth_service.py:51-77`).
The `user` view is correctly populated on login and carries no secret fields.

**Notes / follow-up**

Baseline positive case for the AUTHN suite — the negative/adversarial cases (011-016)
build on this same onboarding fixture.
