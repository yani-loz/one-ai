<!--
  TC-IA-013 — a deactivated account must not be able to log in (generic 401).
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-IA-013: A deactivated (soft-deleted) account cannot log in — generic 401

| Field | Value |
|---|---|
| **ID** | TC-IA-013 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authentication / login (AUTHN) |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Verify that after a user is deactivated via `DELETE /users/{id}` (soft delete →
`is_active=False`), a subsequent login with that user's CORRECT password is rejected with
the generic 401 — the `is_active` gate in `auth_service.login` holds even when the
password is correct.

## Break hypothesis
Attacker bet: a deactivated account still logs in (the `not user.is_active` check is
missing/short-circuited), returning 200 + tokens — meaning soft-delete does not actually
revoke access. Or it returns a distinguishing message ("account disabled") that leaks the
account exists and is merely deactivated.

## Preconditions
Live stack. Harness onboards a fresh org `authn013-<stamp>` (admin), the admin creates a
`member` user `victim-authn013-<stamp>@oneai.dev`, confirms that member can log in (200),
then the admin `DELETE /users/{member_id}` to deactivate it.

## Steps
1. Onboard org, admin creates member; sanity login as member → 200.
2. Admin `DELETE /users/{member_id}` → expect 204.
3. POST `/auth/login` with the member's CORRECT email + password.
4. Inspect status + body. Cross-check DB `is_active` for ground truth.

## Expected result
- Step 2 → `204`.
- Step 3 → `401` with body `{"detail": "Invalid email or password."}`.
- DB row for the member has `is_active = f`.

## Harness
Script: `harness/tc_013.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_013.py`

---

## Execution result

- **Run at:** 2026-05-31 11:52 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> Before deactivation the member logs in (200). After `DELETE /users/{id}` (204), the same
> correct credentials are rejected with 401 and the generic message. The `is_active` gate
> blocks login despite a correct password. DB confirms `is_active = f`.

**Evidence**

```
== onboard == 201 admin=admin-authn013-19e7d32a63b41fe@oneai.dev
== create member == 201 member_id=8884f575-cec4-4931-b79c-8a36dbad5560 email=victim-authn013-19e7d32a63b41fe@oneai.dev
== member login BEFORE deactivate == 200  (tokens issued)
== DELETE /users/{member_id} == 204
== member login AFTER deactivate (correct password) == 401
   body: {'detail': 'Invalid email or password.'}
MEMBER_ID_FOR_DB_CHECK=8884f575-cec4-4931-b79c-8a36dbad5560
RESULT(api): PASS

-- DB ground-truth (host: docker compose exec -T db psql -U oneai -d oneai) --
                  id                  |                   email                   | is_active
--------------------------------------+-------------------------------------------+-----------
 8884f575-cec4-4931-b79c-8a36dbad5560 | victim-authn013-19e7d32a63b41fe@oneai.dev | f
(1 row)
```

**Verdict**

Defense held. `DELETE /users/{id}` is a soft delete and `auth_service.login`
(`auth_service.py:67` — `not user.is_active`) rejects the now-inactive account with the
same generic 401 as any other failure, so soft-delete genuinely revokes login. No
"account disabled" distinction (no enumeration of deactivated accounts).

**Notes / follow-up**

Access-token longevity after deactivation (a token minted before the DELETE) is a
separate concern; this case covers fresh-login revocation only.
