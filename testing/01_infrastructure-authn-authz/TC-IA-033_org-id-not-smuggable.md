# TC-IA-033: A smuggled org_id in the POST /users body is ignored (org forced from token)

| Field | Value |
|---|---|
| **ID** | TC-IA-033 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Cross-tenant isolation (hardest rule) |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern → ✅ **HARDENED** (2026-05-31) |
| **Finding tag** | NEW |

## Objective
`POST /users` must always place the new user in the **caller's** org, taking `org_id` from
the verified token. An attacker-supplied `org_id` field in the request body must be
ignored — a company-admin of A must not be able to inject a user into org B by smuggling
`org_id=B`.

## Break hypothesis
`UserCreateRequest` declares only `email/full_name/role/password` and does **not** set
`extra="forbid"`, so Pydantic silently drops the extra `org_id`. The bet is that the drop
is the only thing protecting us — and to *prove* no other code path reads a body `org_id`.
If the service ever read `payload.org_id` (or the schema gained the field), the user would
land in B: a cross-tenant write. We test the live behavior, not just the schema.

## Preconditions
- Live stack; suite `tenant` orgs A and B. Admin A authenticated. Demo org untouched.

## Steps
1. Onboard A and B; record B's org id; capture B's user-list size before the attack.
2. Admin A `POST /users` with body
   `{email, full_name, role:"member", password, "org_id": <B_org_id>}`.
3. Inspect the created user's `org_id`; confirm it equals A (not B).
4. `GET /users` in A (smuggled email present) and in B (list unchanged, same size).

## Expected result
- `201`; created user `org_id == A_org_id`.
- Smuggled email appears in A's list, **absent** from B's list; B's list size unchanged.

## Harness
Script: `harness/tc_033.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_033.py`

---

## Execution result

- **Run at:** 2026-05-31 11:41 local
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** NEW

**Actual behavior**

> The smuggled `org_id=B` was ignored. The created user landed in org A
> (`org_id == A_org`), appeared only in A's list, and B's list was byte-for-byte unchanged
> (size 1 → 1, same single B-admin email).

**Evidence**

```
A org: a73bbe81-42e7-49f1-bd98-5bc0ddab79be
B org (smuggle target): c06f8a00-8ff5-456d-888a-1c8e460f7b03
B list size BEFORE: 1
SMUGGLE POST status: 201
SMUGGLE POST body: {"id":"0ba5b7df-7a19-4916-883c-d09bc3867269","email":"tenant-smuggle-19e7d31bd4c5327@tenant.oneai","full_name":"Smuggled User","role":"member","is_active":true,"org_id":"a73bbe81-42e7-49f1-bd98-5bc0ddab79be","created_at":"2026-05-31T08:41:13.942909Z"}
created user org_id: a73bbe81-42e7-49f1-bd98-5bc0ddab79be
landed in A? True
landed in B (BAD)? False
A list emails: ['tenant-a-admin-19e7d31bd4c5327@tenant.oneai', 'tenant-smuggle-19e7d31bd4c5327@tenant.oneai']
B list emails: ['tenant-b-admin-19e7d31bd4c5327@tenant.oneai']
smuggle email in A? True
smuggle email in B (BAD)? False
B list size AFTER: 1 (must equal BEFORE)
```

**Verdict**

Defense **held**. `UserService.create_user` constructs the `User` with
`org_id=org_id` where `org_id` is the route's `principal.org_id`
(`user_service.py:66-72`, `user_routes.py:43-50`); it never reads a body `org_id`. The
`org_id` key in the body is dropped by Pydantic (`UserCreateRequest` has no such field).
The user landed in A; B was untouched — so the behaviour is **correct today**. Recorded
as ⚠️ Pass-with-concern (not a plain Pass) because the protection rests on Pydantic's
*default* silent-drop of unknown fields rather than an explicit guard; the **NEW** tag
attaches to that defense-in-depth gap (a previously-untracked hardening note), not to any
reproduced defect.

**Notes / follow-up**

The protection currently depends on Pydantic's *default* drop of unknown fields. A
defense-in-depth nit (not a defect today): adding `model_config = ConfigDict(extra="forbid")`
to `UserCreateRequest` would make a smuggled field a loud `422` rather than a silent drop,
and would prevent a future refactor from accidentally binding a body `org_id`. Note the
forged-token path (TC-IA-035) bypasses this entirely by setting `org_id` in the JWT itself.

---

## Remediation (2026-05-31) — ✅ HARDENED

`extra="forbid"` (`model_config = ConfigDict(extra="forbid")`) added to all identity request models (`UserCreateRequest`, `UserUpdateRequest`, `LoginRequest`, `RefreshRequest`, `LogoutRequest`, `PlatformLoginRequest`, `OrganizationCreateRequest`), so a smuggled field (e.g. `org_id`) is now a loud **422** rather than a silent drop — closing the defense-in-depth gap this case flagged. (The forged-token cross-tenant path, TC-IA-035, is separately tracked — it sets `org_id` in the JWT, not the body.)

- **Code:** the `model_config = ConfigDict(extra="forbid")` lines across the identity `schemas/`.
- **Regression test:** `test_user_routes.py::test_create_user_unknown_field_returns_422`.
- **Tracked:** DYN-04.
