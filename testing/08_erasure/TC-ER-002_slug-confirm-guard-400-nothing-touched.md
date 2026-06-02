<!--
  TC-ER-002 — AC5: slug-confirmation guard, nothing touched. Suite HOLD.
-->

# TC-ER-002: Slug-confirmation mismatch (400) deletes nothing

| Field | Value |
|---|---|
| **ID** | TC-ER-002 |
| **Target** | 08 — GDPR erasure + compliance export (PC-06) |
| **Suite** | HOLD — legal-hold-beats-erasure + slug guard |
| **Type** | Negative / Boundary |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove ⭐ PC-06-AC5: a GitHub-style slug-confirmation guard — `erase_org` with
`confirm_slug != org.slug` returns **400** and deletes nothing. This is the accidental-
destruction guard: an operator who fat-fingers (or pastes the wrong org's id) cannot wipe a
tenant.

## Break hypothesis
The slug check is cosmetic or runs AFTER the destructive deletes — so a mismatched confirmation
still erases (or partially erases) the org. A 200/404/500 instead of 400, or any drop in users
/ change of status / an `org.erased` row, is the defect.

## Preconditions
- Live stack `:8000`. Fresh run-stamped org `S` provisioned this run (slug `hold-er2-<stamp>`).
- Org is `active`, not under legal hold, has 1 user.

## Steps
1. Provision org `S`.
2. psql baseline: users for `S`, status, `org.erased` row count.
3. `erase_org(S, confirm_slug='not-the-slug-<stamp>')` with a valid platform password → expect
   **400** (the password is valid-format so Pydantic does not 422 first; the service checks slug
   FIRST and 400s before the password/legal-hold checks).
4. psql after: users intact, status `active`, no `org.erased` row.

## Expected result
- Erase → HTTP **400**, body mentions the slug/confirmation mismatch (`ErasureConfirmationError`).
- psql: `users` count unchanged, `organizations.status` = `active`,
  `count(audit_log WHERE action='org.erased' AND org_id=S) == 0`.

## Harness
Script: `harness/tc_002.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_002.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Erase with a deliberately wrong `confirm_slug` returned **400** with body
> `{"detail":"Confirmation does not match the organization's slug."}`. The org was untouched:
> 1 user still present, status still `active`, zero `org.erased` rows. The slug guard fired
> before any delete.

**Evidence**

```
BASELINE: {'users': 1, 'status': 'active', 'erased_rows': 0}
ERASE (wrong slug): 400 {"detail":"Confirmation does not match the organization's slug."}
AFTER: {'users': 1, 'status': 'active', 'erased_rows': 0}
RESULT 400: True | nothing_touched: True
VERDICT: PASS
ORG_ID: b1f19c1a-dc4d-4c78-800f-82841ec50ca3 SLUG: hold-er2-19e8477fbc993ef
```

> The erase carried a valid-format `password` (so Pydantic did not 422 first); the service's
> slug check fired before the password/legal-hold checks, returning 400.

**Verdict**

The defense held. The slug-confirmation guard (`erasure_service.py`, `ErasureConfirmationError`
→ 400) fires immediately after the `FOR UPDATE` load and BEFORE the password check, legal-hold
check, and any delete. Accidental destruction is blocked. CONFIRMS-FIXED for AC5 against the
live stack.

**Notes / follow-up**

Org `S` left intact (active). The slug check correctly precedes both the password (403) and
legal-hold (409) guards — the ordering is corroborated in TC-ER-003.
