# TC-ER-020: Append-only audit_log RETAINED through erasure (AC4)

| Field | Value |
|---|---|
| **ID** | TC-ER-020 |
| **Target** | GDPR erasure + compliance export (PC-06) |
| **Suite** | RETAIN — append-only audit retained + compliance export |
| **Type** | Positive |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-06-AC4: the immutable `audit_log` SURVIVES erasure (it is never deleted), and the
erasure itself appends a new `org.erased` row. The pre-erase trail (onboard + login + a support
request/approve + a status PATCH) must still be readable after the org is erased, and a new
`org.erased` event must be present — the documented Art. 17(3) retention, not a leak.

## Break hypothesis
The attacker's bet: erasure either (a) cascades into / truncates the org's audit rows so the
trail is gone, or (b) the `org.erased` event is never recorded (a successful destructive action
is silently unlogged). Either makes the certificate dishonest. REFUTES-FIX = pre-erase rows
missing OR `org.erased` absent.

## Preconditions
- Live stack `:8000`; harness inside the backend container over stdin.
- Fresh run-stamped org `R` (slug prefix `retain-er020`) provisioned this run, erased ONLY by
  this case. Never touches demo/globex/another suite's org.
- Platform auth via the canonical `/platform/login` (the demo admin was briefly re-seeded by a
  concurrent suite mid-run, then recovered — login healthy at execution time).

## Steps
1. Platform login; `provision_company` → org `R` (emits `org.onboard` + `auth.login.success`).
2. `request_support(R)` → capture `grant_id`; `company_approve` (emits `support.requested` +
   `support.approved`).
3. `patch_status(R, "suspended")` (emits `org.suspend`) — last, just before erase.
4. Snapshot pre-erase audit via `get_org_audit(R)`.
5. `erase_org(R, confirm_slug=R.slug, password=PLATFORM_PW)` → 200 certificate.
6. `get_org_audit(R)` again → assert pre-erase actions survive AND `org.erased` appended.
7. psql: `SELECT action, count(*) FROM audit_log WHERE org_id='R' GROUP BY action`.

## Expected result
- Erase → 200, `audit_log_retained: true`.
- Post-erase audit → 200 with the same pre-erase actions PLUS `org.erased`.
- psql: audit_log rows for `R` > 0, including one `org.erased`; 0 users + 0 un-scrubbed decider
  emails left.

> NOTE — the running server's `ErasureRequest` requires a re-auth `password` field (3 fields:
> reason + confirm_slug + password) that the on-disk 2-field schema and the stale
> `_common.erase_org` helper omit. The harness posts directly with `password=PLATFORM_PW`.

## Harness
Script: `harness/tc_020.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_020.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 21:43 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The full pre-erase trail survived the erase. Pre-erase: 5 events (org.suspend,
> support.approved, support.requested, auth.login.success, org.onboard). Post-erase: 6 events —
> all 5 intact PLUS a new `org.erased`. Certificate: `users_erased:1`, `tokens_deleted:1`,
> `support_decider_emails_scrubbed:1`, `audit_log_retained:true`. psql confirmed 6 audit_log
> rows for the org incl `org.erased`, and 0 users / 0 un-scrubbed decider emails left. The
> retained login row carries `actor_email` (tenant admin) + `ip_address` — the documented
> Art. 17(3) table-scope retention.

**Evidence**

```
=== TC-ER-020 ===
ORG_ID: 823b9cba-da24-4d58-b2f2-03283947a828
SLUG: retain-er020-19e847f1cbd07f7
REQUEST_SUPPORT: 201
APPROVE: 200 decided_by_email: admin-retain-er020-19e847f1cbd07f7@oneai.dev
PATCH_STATUS: 200 status: suspended
PRE_ERASE_AUDIT: 200 actions: ['org.suspend', 'support.approved', 'support.requested', 'auth.login.success', 'org.onboard']
ERASE: 200
CERT users_erased: 1 tokens_deleted: 1 decider_scrubbed: 1 audit_log_retained: True
POST_ERASE_AUDIT: 200 actions: ['org.erased', 'org.suspend', 'support.approved', 'support.requested', 'auth.login.success', 'org.onboard']
PRE_ROWS_SURVIVED: True
HAS_ORG_ERASED: True
RETAINED_LOGIN actor_email: admin-retain-er020-19e847f1cbd07f7@oneai.dev ip: 127.0.0.1
RESULT: PASS

--- psql corroboration ---
       action       | count
--------------------+-------
 auth.login.success |     1
 org.erased         |     1
 org.onboard        |     1
 org.suspend        |     1
 support.approved   |     1
 support.requested  |     1
(6 rows)

 users_left | decider_emails_left
------------+---------------------
          0 |                   0
```

**Verdict**

The defense held. The immutable `audit_log` survives erasure intact and the destructive action
is itself logged (`org.erased`) — `erasure_service.py:103-125` deletes tokens/users + scrubs +
offboards but never touches `audit_log`, and appends the `org.erased` row in the same atomic
transaction; `audit_log_retained=true` (`erasure_service.py:136`) is honest. The retained
`actor_email`/`ip_address` is the DOCUMENTED Art. 17(3) table-scope retention the PR-6 review
dismissed — NOT a NEW finding. Confirms PC-06-AC4.

**Notes / follow-up**

`actor_email`/`ip_address` pseudonymization at write time is the tracked real fix
(`docs/FIX_BEFORE_PROD.md`, Audit & compliance §, item c). Pairs with TC-ER-022 (the retained
trail is still exportable after erase).
