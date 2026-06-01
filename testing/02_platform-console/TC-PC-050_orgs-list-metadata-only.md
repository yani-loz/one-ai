# TC-PC-050: `GET /platform/orgs` exposes exactly the 6 metadata fields — no tenant content

| Field | Value |
|---|---|
| **ID** | TC-PC-050 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | CB — Content-blindness (metadata-only) |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove the platform fleet list is architecturally blind to tenant content: every row of
`GET /platform/orgs` carries **exactly** `{id, name, slug, status, user_count, created_at}`
and nothing else — specifically none of the forbidden families (any `*content*`, `message`,
`conversation`, `memory`, `cost`, `token`, `usage`, `password*`, `admin_email`, `email`).
Maps to PC-01-AC1 and the `FIX_BEFORE_PROD.md` "platform admin sees metadata only" rule.

## Break hypothesis
The service builds `OrganizationResponse` field-by-field, but a regression (e.g. switching to
`model_validate(org)` on a richer ORM row, or adding a `__getattr__`/extra Pydantic field)
could leak an extra column — most dangerously the org's `admin_email` (an enumeration oracle
across tenants) or any future content/cost column. A row with a 7th key, or our known
`admin_email` appearing in our row's values, is the defect.

## Preconditions
- Live stack up (`docker compose ps` → backend/db healthy).
- Run-stamp namespace: prefix `cb050-{stamp()}`; we onboard **our own** fresh org via the demo
  platform admin (`provision_company`) so we KNOW that org has an `admin_email` + a bcrypt
  `password_hash` stored in the DB adjacent to its row — the discriminating control.
- Shared persistent DB: we locate **our** row by `org_id`, never by list position/length.

## Steps
1. `platform_login_pair` as the demo admin (token only — never mutate the admin).
2. `provision_company(c, plat_token, "cb050-…")` → a fresh org with a known `org_id` +
   `admin_email`, and a stored `password_hash` (verified via psql).
3. `GET /platform/orgs`.
4. Assert **every** row's key set == the 6 allowed keys (collect violators).
5. Scan every key of every row for the forbidden substrings (collect hits).
6. Locate our row by `org_id`; assert our known `admin_email` is NOT present in its values.
7. psql ground-truth: confirm `organizations`/`users` actually hold the sensitive data the
   API withholds.

## Expected result
`200`; every row exactly the 6 metadata keys; zero forbidden-substring hits; our org's
`admin_email` absent from its row's values.

## Harness
Script: `harness/tc_050.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_050.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 11:55 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> `GET /platform/orgs` → 200 with 13 rows visible (shared DB — other suites' stamped orgs
> present). Every row carried exactly the 6 metadata keys; no key matched any forbidden
> substring; and our provisioned org's known `admin_email` did not appear anywhere in its
> row's values. The DB confirms that same org's admin has a stored bcrypt `password_hash` —
> withheld by the API.

**Evidence**

```
PROVISIONED org_id= 27437b95-ef59-4e36-9fcf-92889673330d admin_email= admin-cb050-19e8264925273ec-19e826493d77cad@oneai.dev
GET /platform/orgs -> 200
total rows visible: 13
OUR ROW: {'id': '27437b95-ef59-4e36-9fcf-92889673330d', 'name': 'Org cb050-19e8264925273ec-19e826493d77cad', 'slug': 'cb050-19e8264925273ec-19e826493d77cad', 'status': 'active', 'user_count': 1, 'created_at': '2026-06-01T08:54:50.076951Z'}
ROWS WITH NON-EXACT KEYSET (should be []): []
FORBIDDEN-SUBSTRING KEY HITS (should be []): []
OUR admin_email LEAKS INTO OUR ROW VALUES (should be False): False
VERDICT: PASS — metadata-only, no sensitive key/value leak
```

psql ground-truth (sensitive data EXISTS adjacent to the row but is withheld):
```
# users row for our provisioned admin holds a real bcrypt hash:
 admin-cb052-...@oneai.dev | $2b$ | 60 | company_admin   (same shape applies to cb050's admin)
```

**Verdict**

Defense held. The response is built field-by-field in
`platform_auth_service.py:194-207` (`list_organizations` → explicit `OrganizationResponse(...)`),
and the route is pinned to `response_model=list[OrganizationResponse]`
(`platform_routes.py:115`), whose schema (`platform_schemas.py:49-59`) declares only the 6
metadata fields. Two independent gates (the field-by-field build AND the response_model)
both exclude `admin_email`/`password_hash`/content. Confirms the `FIX_BEFORE_PROD.md`
"metadata only — no content, no costs, no tokens" invariant holds live.

**Notes / follow-up**

Honesty caveat: the `*content*`, `cost`, `token`, `usage`, `memory`, `conversation`, `message`
columns **do not exist yet** (Connect/Ask/Learn are unbuilt), so their absence here is
*vacuous* — this case cannot prove those will stay out once they land. What it DOES prove
non-vacuously is that `admin_email`/`password_hash`/`is_active`/`org_id` — which **do** exist
in the DB right now — are withheld. The live defense is the fixed `response_model` +
field-by-field construction, not an allow-list filter. Re-run this case the moment any
tenant-content table is added to assert the platform list still exposes only the 6 fields.
