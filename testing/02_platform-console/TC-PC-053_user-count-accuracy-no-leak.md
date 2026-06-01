# TC-PC-053: `user_count` is accurate without leaking per-user data

| Field | Value |
|---|---|
| **ID** | TC-PC-053 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | CB — Content-blindness (metadata-only) |
| **Type** | Positive |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Prove `user_count` is a faithful aggregate, not a content leak: onboard an org (admin = user
#1), create 2 members as its company_admin (total 3), then `GET /platform/orgs` and assert
that org's row shows `user_count == 3` while STILL exposing only the 6 metadata fields — no
per-user data (emails/names) bleeds into the row.

## Break hypothesis
The aggregate is computed by `list_all_with_user_counts`. The genuinely adversarial failure is
**over-counting via a wrong join** (e.g. counting users across orgs, or a cartesian blow-up) —
that would be a cross-tenant *inference* leak (one org's count revealing another's membership).
A `user_count != 3`, or any member email appearing in the row, is the defect.

## Preconditions
- Live stack up.
- Run-stamp namespace: prefix `cb053-{stamp()}`; fresh org via the demo platform admin; its
  company_admin token is used to create 2 members with prefix+stamp-namespaced emails.
- psql ground-truth: filtered by OUR `org_id` (never a global `COUNT(*)` — a concurrent actor
  could pollute it).

## Steps
1. `provision_company` → fresh org (admin = user #1) + company_admin token.
2. `POST /users` ×2 (role `member`, namespaced emails) with the admin token.
3. `GET /platform/orgs`; locate our row by `org_id`.
4. Assert `user_count == 3`; assert row key set == the 6 metadata fields; assert no member
   email appears in the row's values.
5. psql: `SELECT count(*) WHERE org_id = <ours>` == 3.

## Expected result
Both `POST /users` → `201`; our row `user_count == 3`; exactly the 6 metadata keys; no
per-user data in the row.

## Harness
Script: `harness/tc_053.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_053.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 11:55 local
- **Result:** ✅ Pass
- **Finding tag:** — (positive contract test)

**Actual behavior**

> Both members created (201). Our org's `/platform/orgs` row showed `user_count == 3` with
> exactly the 6 metadata keys and no member email in its values. psql confirms exactly 3 users
> for our `org_id` (`{company_admin, member, member}`) — the count is a faithful per-org
> aggregate, not an over-join.

**Evidence**

```
PROVISIONED org_id= 17213a5b-cf36-4ae2-95da-0744d19e778f (admin counts as user #1)
POST /users [0] member-0-cb053-...@oneai.dev -> 201 {... 'role': 'member', 'org_id': '17213a5b-...'}
POST /users [1] member-1-cb053-...@oneai.dev -> 201 {... 'role': 'member', 'org_id': '17213a5b-...'}
GET /platform/orgs -> 200
OUR ROW: {'id': '17213a5b-cf36-4ae2-95da-0744d19e778f', 'name': 'Org cb053-...', 'slug': 'cb053-...', 'status': 'active', 'user_count': 3, 'created_at': '2026-06-01T08:55:02.923113Z'}
user_count == 3: True (actual: 3 )
ROW KEYSET == 6 metadata fields: True ['created_at', 'id', 'name', 'slug', 'status', 'user_count']
PER-USER EMAIL LEAKS INTO ROW (should be False): False
VERDICT: PASS — accurate count, metadata-only
```

psql ground-truth — exactly 3 users for OUR org_id (filtered, never global COUNT(*)):
```
 user_count |             roles
------------+-------------------------------
          3 | {company_admin,member,member}
```

**Verdict**

Defense held. `list_organizations` (`platform_auth_service.py:194-207`) maps each
`(org, user_count)` tuple from `list_all_with_user_counts` into the 6-field
`OrganizationResponse`. The DB-filtered count (3) matches the reported `user_count` (3) and no
per-user identity bleeds through — the aggregate exposes a number, not the membership. The
6-field metadata contract holds even as the count changes.

**Notes / follow-up**

The count is an aggregate scalar, so it conveys *how many* without *who* — acceptable
metadata. Re-run this case if the count query is ever refactored (a join change is the most
likely source of a cross-tenant over-count). No defect.
