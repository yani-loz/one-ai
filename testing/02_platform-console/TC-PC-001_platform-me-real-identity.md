# TC-PC-001: GET /platform/me returns the demo admin's real identity (exactly {id,email,full_name})

| Field | Value |
|---|---|
| **ID** | TC-PC-001 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PSES — Session lifecycle |
| **Type** | Positive |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-02-AC2: a valid platform access token resolves the admin's **own** server-verified
identity at `GET /platform/me`, and the response is **exactly** `{id, email, full_name}` — no
`password_hash`, no org fields (content-blindness of the /me view).

## Break hypothesis
Either (a) `/platform/me` leaks an extra field — most dangerously `password_hash` —
because `PlatformAdminResponse` is built from the ORM row via `model_validate`, or (b) it
synthesises identity instead of reading the real row. A violation = any key beyond the 3,
or an email/name that is not the demo admin's.

## Preconditions
- Live stack up; demo platform admin `super@ethera.ai` seeded (never mutated).
- Run-stamp: PSES suite, stamp `tw06012c3` namespace (no orgs created by this case).

## Steps
1. `platform_login_pair()` → (access, refresh).
2. `GET /platform/me` with the access token.
3. Assert 200; assert key set == `{id,email,full_name}`; assert `password_hash` absent;
   assert email == `super@ethera.ai`.

## Expected result
`200`; body exactly `{id, email, full_name}` with the demo admin's real identity; no hash.

## Harness
Script: `harness/tc_001.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_001.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack, real uvicorn :8000)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> `GET /platform/me` with a valid platform access token returned 200 with exactly the three
> contract fields and the demo admin's real identity ("Ethera Super Admin"). No password hash
> or org field appeared.

**Evidence**

```
STATUS: 200
BODY  : {'id': '609f2b17-bee9-4f7f-a26d-cb08f666497a', 'email': 'super@ethera.ai', 'full_name': 'Ethera Super Admin'}
KEYS  : ['email', 'full_name', 'id']
EXACT-3-FIELDS: True
NO-PASSWORD-HASH: True
EMAIL-IS-DEMO-ADMIN: True
```

**Verdict**

Defense held. `build_admin_view_by_id` (`platform_auth_service.py:126-139`) loads the row by
id and serializes through `PlatformAdminResponse` (`platform_schemas.py:69-82`), whose model
declares only `id/email/full_name` — the password hash cannot leak. AUD-14 / PC-02-AC2
confirmed live.

**Notes / follow-up**

Content-blindness of the /me view proven once. Companion: CB suite covers `/platform/orgs`.
