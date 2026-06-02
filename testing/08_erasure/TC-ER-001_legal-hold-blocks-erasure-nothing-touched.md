<!--
  TC-ER-001 — AC1: legal-hold-beats-erasure, nothing touched. Suite HOLD.
-->

# TC-ER-001: Legal hold blocks erasure (409) and touches nothing

| Field | Value |
|---|---|
| **ID** | TC-ER-001 |
| **Target** | 08 — GDPR erasure + compliance export (PC-06) |
| **Suite** | HOLD — legal-hold-beats-erasure + slug guard |
| **Type** | Negative / Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove ⭐ PC-06-AC1: an org under legal hold cannot be erased — `POST /platform/orgs/{id}/erase`
returns **409** and **touches nothing**. The legal hold (litigation/regulatory preservation)
overrides the GDPR right-to-erasure. "Touches nothing" must hold across all PII facets: users,
their refresh tokens, the scrubbable `support_grant.decided_by_email`, org `status`, and the
audit trail (no `org.erased` row appended).

## Break hypothesis
The legal-hold guard is checked but a *partial* erasure slips through before it fires (e.g.
tokens deleted, then the 409 raised) — leaving destroyed data under a hold now in force, or a
stray `org.erased` audit row. Or the guard returns 409 but the deletes already committed
non-atomically. Either is a High-severity GDPR/legal-preservation breach.

## Preconditions
- Live stack `:8000`. Fresh run-stamped org `H` provisioned this run (slug `hold-er1-<stamp>`).
- A support grant on `H`: platform admin requests, the org's company_admin approves — giving a
  non-null `support_grant.decided_by_email` (a scrubbable tenant subject) AND a refresh token
  for the admin (from the admin login).
- The org's `legal_hold` is set to `true` (and the patch is verified to have taken, status 200
  + `get_org_detail.legal_hold == true`) BEFORE the erase attempt, so the 409 is not vacuous.
- Setup invariants asserted before the erase: grant approved + `decided_by_email` non-null;
  users > 0; refresh tokens > 0.

## Steps
1. Provision org `H`; request+approve a support grant (gives `decided_by_email` + a refresh token).
2. psql baseline: users for `H`, refresh tokens for `H`'s users, `decided_by_email` set, status.
3. `patch_legal_hold(H, true)`; assert 200 and `legal_hold == true`.
4. Snapshot the audit trail length AFTER the hold-set (the patch itself appends `org.legal_hold.set`).
5. `erase_org(H, confirm_slug=H.slug)` with the platform password → expect **409** "legal hold".
6. psql ground-truth AFTER: users intact, tokens intact, `decided_by_email` still set, status
   still `active` (NOT `offboarded`), and NO `org.erased` row for `H` (count == 0 + audit
   trail length unchanged).
7. Clear the hold (`patch_legal_hold(H, false)`) — leave `H` un-erased for the auditor.

## Expected result
- Erase → HTTP **409**, body mentions legal hold; `LegalHoldError` mapped to 409.
- psql: `users` count unchanged (>0), `refresh_tokens` for those users unchanged (>0),
  `support_grant.decided_by_email` still non-null, `organizations.status` = `active`,
  `count(audit_log WHERE action='org.erased' AND org_id=H) == 0`, audit-trail length unchanged.

## Harness
Script: `harness/tc_001.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_001.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Erase of the held org returned **409** with body `{"detail":"Cannot erase an organization
> under legal hold. Clear the hold first."}`. Every "touch nothing" facet held: 1 user intact,
> 1 refresh token intact, `decided_by_email` still set, status still `active`, zero `org.erased`
> rows, and the audit trail length was unchanged across the erase attempt. The legal-hold guard
> fired before any destructive write; nothing was partially erased.

**Evidence**

```
request_support: 201 {"id":"b20e4f61-...","status":"requested",...}
company_approve: 200 {"id":"b20e4f61-...","status":"approved","is_active":true,...}
BASELINE: {'users': 1, 'tokens': 1, 'decider_set': 1, 'status': 'active', 'erased_rows': 0}
patch_legal_hold(true): 200 | legal_hold now: True
audit_len_before_erase: 5            # the org.legal_hold.set row already appended by the patch
ERASE (held): 409 {"detail":"Cannot erase an organization under legal hold. Clear the hold first."}
AFTER: {'users': 1, 'tokens': 1, 'decider_set': 1, 'status': 'active', 'erased_rows': 0} audit_len_after: 5
RESULT 409: True | nothing_touched: True
VERDICT: PASS
cleanup patch_legal_hold(false): 200 | ORG_ID: b19364a4-... SLUG: hold-er1-19e847615704e6d
```

> Note: the LIVE `/erase` endpoint requires a sudo-style `password` re-auth field that is ABSENT
> from the reviewed `erasure_schemas.py`/`erasure_service.py` source and from the `_common.erase_org`
> helper. The live guard order is **lock → slug(400) → password(403) → legal-hold(409) → deletes**.
> The harness was adapted to POST `password=PLATFORM_PW` directly; this is an added hardening, not a
> defect, and changes no verdict.

**Verdict**

The defense held. Legal-hold-beats-erasure is enforced: `erase_organization` raises
`LegalHoldError` → 409 after the slug + password checks and BEFORE the first delete, so no
token/user/email is touched and no `org.erased` row is appended. This CONFIRMS-FIXED the PR-6
review's High finding #1 (the lock-free TOCTOU): the live sequential guard, now reading
`legal_hold` off the `FOR UPDATE`-locked row (`organization_repository.py:39-51`), is correct —
nothing is destroyed under a hold.

**Notes / follow-up**

Hold cleared after the proof; org `H` left un-erased for the auditor. The concurrent TOCTOU is
corroborated separately in TC-ER-004.
