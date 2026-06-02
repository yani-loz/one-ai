# TC-ER-011: PII-left-behind sweep via psql ground-truth (AC2/AC3)

| Field | Value |
|---|---|
| **ID** | TC-ER-011 · **Target** Erasure (PC-06) · **Suite** ERASE |
| **Type** | Adversarial · **Severity if fail** High · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-FIXED |

## Objective
After erasure, **no undocumented tenant PII survives**: zero users, zero orphan refresh tokens,
`support_grant.decided_by_email` NULL (scrubbed) but `requested_by_email` (Ethera staff) kept, org row
retained at `offboarded`. A surviving user / unrevoked token / un-nulled decider email would be a NEW defect.

## Steps / Harness
Provision E2; request+approve a support grant (sets `decided_by_email`); `erase_org(E2)`; psql sweep. `harness/tc_011.py`.

## Execution result
- **Run at:** 2026-06-01 · **Result:** ✅ Pass · **Tag:** CONFIRMS-FIXED

**Evidence**
```
psql: surviving_users=0; org status=offboarded; orphan_user_tokens=0
support_grant: requested_by_email=super@ethera.ai  decided_by_email=(NULL)
cert: users_erased=1 tokens_deleted=1 support_decider_emails_scrubbed=1
```

**Verdict**
Defense held — no undocumented leftover. tokens deleted first (`delete_for_org_users`), decider email
scrubbed (`scrub_decider_emails`), users deleted (`delete_all_in_org`), org offboarded
(`erasure_service.py:115-118`). Retained `decided_by_user_id` (bare pseudonymous UUID) + retained
`audit_log` are the **documented** settled design (PR-6 review), correctly not flagged. PC-06-AC2/AC3 confirmed.
