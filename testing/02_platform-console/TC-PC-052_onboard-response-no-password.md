# TC-PC-052: Onboard 201 response carries no password / hash despite a password being submitted

| Field | Value |
|---|---|
| **ID** | TC-PC-052 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | CB — Content-blindness (metadata-only) |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove the onboarding round-trip never reflects the submitted credential: in the `201` body of
`POST /platform/orgs`, the `admin` object has **no** `password` / `password_hash` field and
does not echo the plaintext password, even though a password was just submitted and a bcrypt
hash was just written to the DB.

## Break hypothesis
The admin is serialized via `UserResponse.model_validate(admin)` on the freshly-INSERTed ORM
`User` (whose `password_hash` column is populated). If `UserResponse` ever gained a
`password_hash` field (or `model_config` were loosened), the hash would round-trip straight
back to the caller. Equally, a careless handler could echo the submitted plaintext. Either is
the defect.

> **Note (avoids a false FAIL):** `UserResponse` legitimately has **7** fields
> (`id, email, full_name, role, is_active, org_id, created_at`). This case asserts the
> **negative only** (no `pass*` key, no echoed plaintext, no `$2…` value) — an exact-set
> assertion here would wrongly flag the legitimate 7-field shape.

## Preconditions
- Live stack up.
- Run-stamp namespace: prefix `cb052-{stamp()}`; fresh org + admin onboarded via the demo
  platform admin. Submitted password = `DEFAULT_PW` (`Valid-Pass-2026!`).
- psql ground-truth: the new `users` row holds a real bcrypt `password_hash` — proving a hash
  exists and is withheld (not simply absent).

## Steps
1. `platform_login_pair` → platform token.
2. `onboard_org(...)` with a known submitted password.
3. Assert `201`.
4. Assert no key in `admin` matches `/pass/i`; assert `"password"` appears nowhere in the
   whole 201 body; assert the submitted plaintext is not echoed; assert no `admin` value is
   bcrypt-shaped.
5. psql: confirm the new `users` row has a populated `password_hash`.

## Expected result
`201`; admin object = the 7 `UserResponse` fields with **no** password/hash; no plaintext echo.

## Harness
Script: `harness/tc_052.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_052.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 11:55 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> `POST /platform/orgs` → 201. The `admin` object held exactly the 7 legitimate
> `UserResponse` fields; no `pass*` key anywhere in the body; the submitted plaintext was not
> echoed; no value was bcrypt-shaped. psql confirms the new `users` row DOES hold a 60-char
> `$2b$` hash — i.e. a hash was created from the submitted password and then withheld.

**Evidence**

```
POST /platform/orgs -> 201
FULL 201 BODY: {'organization': {'id': 'ef4bdee0-5e02-4098-a73f-2e99a9e95d31', 'name': 'Org cb052-19e8264b8cbed6b-19e8264b8cb2dec', 'slug': 'cb052-19e8264b8cbed6b-19e8264b8cb2dec', 'status': 'active', 'user_count': 1, 'created_at': '2026-06-01T08:54:59.935413Z'}, 'admin': {'id': '32f80aa3-87cc-47c9-9e9a-8323218c256e', 'email': 'admin-cb052-19e8264b8cbed6b-19e8264b8cb2dec@oneai.dev', 'full_name': 'CB Onboard Admin', 'role': 'company_admin', 'is_active': True, 'org_id': 'ef4bdee0-5e02-4098-a73f-2e99a9e95d31', 'created_at': '2026-06-01T08:54:59.935413Z'}}
ADMIN OBJECT KEYS: ['created_at', 'email', 'full_name', 'id', 'is_active', 'org_id', 'role']
ADMIN KEYS MATCHING 'pass' (should be []): []
'password' SUBSTRING ANYWHERE IN BODY (should be False): False
SUBMITTED PLAINTEXT PW ECHOED (should be False): False
ANY ADMIN VALUE LOOKS LIKE A BCRYPT HASH (should be False): False
ADMIN KEYS == expected UserResponse 7 fields: True
VERDICT: PASS — no password/hash in onboard response
```

psql ground-truth — the stored hash EXISTS (was created from the submitted password) but is withheld:
```
                         email                         | hash_prefix | hash_len |     role
-------------------------------------------------------+-------------+----------+---------------
 admin-cb052-19e8264b8cbed6b-19e8264b8cb2dec@oneai.dev | $2b$        |       60 | company_admin
```

**Verdict**

Defense held. A bcrypt hash was demonstrably written
(`platform_auth_service.py:174` — `password_hash=hash_password(payload.admin_password)`),
yet the 201 `admin` view returns only the `UserResponse` fields
(`platform_auth_service.py:191` — `UserResponse.model_validate(admin)`;
`user_schemas.py:102-113` declares no `password_hash`). Route pins
`response_model=OrganizationOnboardedResponse` (`platform_routes.py:101-104`). Confirms the
write-side credential never round-trips to the caller.

**Notes / follow-up**

Non-vacuous: the hash provably exists on the row, so the withholding is real, not absence.
Related deferral — `FIX_BEFORE_PROD.md` "Replace admin-set passwords with an email-verification
+ invite flow": the admin still *chooses* the user's plaintext password (it just isn't
reflected back). That is a separate, tracked design issue; this case only asserts the response
does not leak the credential.
