# TC-PC-051: `GET /platform/me` returns exactly `{id, email, full_name}` — no hash / org / flags

| Field | Value |
|---|---|
| **ID** | TC-PC-051 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | CB — Content-blindness (metadata-only) |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove the platform admin self-view leaks nothing beyond identity: `GET /platform/me` returns
**exactly** `{id, email, full_name}` — no `password_hash`, no `org_id`, no `is_active`, no
`created_at`/`updated_at`, no `role`. Maps to PC-02-AC2 and the "never includes the password
hash" invariant on `PlatformAdminResponse`.

## Break hypothesis
`build_admin_view_by_id` calls `PlatformAdminResponse.model_validate(admin)` on the full ORM
row. If a field were added to `PlatformAdminResponse` (or `model_config` were loosened to
serialize all attributes), the bcrypt `password_hash` or the `is_active`/`created_at` columns
that exist on the row would surface. Any key beyond the 3, or any value shaped like `$2…`
(bcrypt), is the defect.

## Preconditions
- Live stack up.
- Demo platform admin (`super@ethera.ai`) — used ONLY to log in (token), never mutated.
- psql ground-truth: the `platform_admins` row for that admin carries `password_hash`,
  `is_active`, `created_at`, `updated_at` — the sensitive columns the API must withhold.

## Steps
1. `platform_login_pair` → platform access token.
2. `GET /platform/me` with the bearer token.
3. Assert key set == `{id, email, full_name}` exactly.
4. Scan keys for forbidden substrings (`password`, `org_id`, `is_active`, `created_at`,
   `updated_at`, `role`).
5. Assert no value looks like a bcrypt hash (`$2…`).
6. psql: show the row HAS the withheld columns.

## Expected result
`200`; body exactly `{id, email, full_name}`; no hash; no extra metadata.

## Harness
Script: `harness/tc_051.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_051.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 11:55 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> `GET /platform/me` → 200 with exactly `{id, email, full_name}`. No forbidden key, no
> bcrypt-shaped value. The `platform_admins` DB row for the same `id` (`609f2b17-…`) holds a
> 60-char `$2b$12$` hash plus `is_active`/`created_at` — none of which appears in the response.

**Evidence**

```
GET /platform/me -> 200
BODY: {'id': '609f2b17-bee9-4f7f-a26d-cb08f666497a', 'email': 'super@ethera.ai', 'full_name': 'Ethera Super Admin'}
KEYSET: ['email', 'full_name', 'id']
EXACT 3-KEY SET (id,email,full_name): True
FORBIDDEN-KEY HITS (should be []): []
ANY VALUE LOOKS LIKE A BCRYPT HASH (should be False): False
VERDICT: PASS — identity-only, no hash/org/flags leaked
```

psql ground-truth — the row HAS the sensitive columns the API withholds (note matching `id`):
```
                  id                  |      email      | hash_prefix | hash_len | is_active |          created_at
--------------------------------------+-----------------+-------------+----------+-----------+-------------------------------
 609f2b17-bee9-4f7f-a26d-cb08f666497a | super@ethera.ai | $2b$12$     |       60 | t         | 2026-06-01 07:57:59.378821+00
```

**Verdict**

Defense held. The matching `id` proves the API resolved the *same* DB row that carries the
hash, yet returned only 3 fields. Code path:
`platform_routes.py:88-98` (`response_model=PlatformAdminResponse`) →
`platform_auth_service.py:126-139` (`build_admin_view_by_id` →
`PlatformAdminResponse.model_validate(admin)`) → `platform_schemas.py:69-82`, whose schema
declares only `id/email/full_name` (no `password_hash`). Confirms the documented
"never includes the password hash" invariant holds live.

**Notes / follow-up**

Strong, non-vacuous proof: every withheld column (`password_hash`, `is_active`, `created_at`,
`updated_at`) physically exists on the row today. The protection rests on the narrow
`PlatformAdminResponse` schema — any future field added to it would surface immediately on
this endpoint, so this case should be re-run if `PlatformAdminResponse` ever changes.
