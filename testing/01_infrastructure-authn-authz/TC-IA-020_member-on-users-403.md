# TC-IA-020: Member token on `/users` (GET + POST) → 403 PermissionDenied

| Field | Value |
|---|---|
| **ID** | TC-IA-020 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Negative |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Verify the role gate `require_company_admin` denies a **member** — an authenticated,
real-token caller who is simply not an admin — on the company user-management surface
(`GET /users`, `POST /users`), returning 403, not 200.

## Break hypothesis
The attacker's bet: the role gate is missing or evaluated after the data fetch, so a
member's valid token lists or creates users (200/201). A 200 on GET would be a
cross-role privilege escalation (member reads the whole org's user roster); a 201 on
POST would let a member mint accounts.

## Preconditions
- Live stack at `http://localhost:8000`; RLS inert (app-layer control only).
- Fresh run-stamped org `authz-<stamp>` onboarded via the platform admin
  (`super@ethera.ai`). Admin `authz-admin-<stamp>@example.com` and a real member
  `authz-member-<stamp>@example.com` both created and logged in for real tokens.

## Steps
1. Platform-login; onboard a fresh org (gets a `company_admin`).
2. Admin logs in; creates a `member` in the same org; member logs in → real member token.
3. `GET /users` with the member token.
4. `POST /users` with the member token and a valid, schema-complete body.
5. Control: `GET /auth/me` with the member token (must be 200 — token is otherwise valid).

## Expected result
- `GET /users` → **403** `{"detail":"Company administrator role required."}`.
- `POST /users` → **403** (same), and no user created.
- `GET /auth/me` → **200** (the member token authenticates fine; only the *role* gate blocks).

## Harness
Script: `harness/tc_020.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_020.py`

---

## Execution result

- **Run at:** 2026-05-31 08:40 local
- **Result:** ✅ Pass
- **Finding tag:** — (NA — positive defense; no prior audit finding about the role gate to "confirm fixed")

**Actual behavior**

> Both `GET /users` and `POST /users` with a real member token returned **403**
> `{"detail":"Company administrator role required."}`. The same member token returned
> **200** on `/auth/me`, confirming the token is valid and only the role gate blocked
> the admin surface (not an auth failure). No victim user was created.

**Evidence**

```
[setup] onboard_org -> 201 {"organization":{"id":"c44ed524-...","slug":"authz-19e7d30f755f09f",...},"admin":{...,"role":"company_admin",...}}
[setup] admin login -> 200
[setup] create member -> 201 {"id":"13e57794-...","role":"member","org_id":"c44ed524-...",...}
[setup] member login -> 200
[attack] GET /users (member token) -> 403 {"detail":"Company administrator role required."}
[attack] POST /users (member token) -> 403 {"detail":"Company administrator role required."}
[control] GET /auth/me (member token) -> 200 {"id":"13e57794-...","role":"member",...}
```

**Verdict**

Defense **held**. The role gate `require_company_admin`
(`backend/app/identity/dependencies.py:90-100`) raises `PermissionDeniedError` →
403 (`error_handlers.py:41`) for a `member` principal on both `/users` verbs, while
the upstream token verification still authenticates the member (200 on `/auth/me`).
This is the correct 401-vs-403 split: authentication succeeds, authorization fails.
No prior audit finding claimed this broken; it is a positive defense, confirmed.

**Notes / follow-up**

The gate runs as a FastAPI dependency *before* the body is parsed, so the 403 fires
even on POST regardless of payload — the schema-complete body removed any chance a 422
masked the result. Pairs with TC-IA-021 (admin → 200 control).
