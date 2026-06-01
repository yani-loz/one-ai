<!--
  Test-case: TC-BG-033. Authored before running; Execution result block written back after.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-033: Audit emission — every transition logged; support.approved carries expires_at

| Field | Value |
|---|---|
| **ID** | TC-BG-033 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | AEA — Audience confinement + live expiry + audit + input |
| **Type** | Positive / Adversarial (completeness) |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
PC-05-AC8: every break-glass transition writes to the append-only `audit_log`. After driving
request→approve, request→deny, and request→revoke on one org, the org's audit trail must contain
`support.requested`, `support.approved`, `support.denied`, and `support.revoked`. The
`support.approved` entry's `details` must carry `expires_at` ("logged → expire" — the expiry is
recoverable from the trail without a discrete expiry event).

## Break hypothesis
If a transition path skipped its audit write (or `support.approved` omitted `expires_at` from
`details`), the audit trail would be an incomplete record of who-let-whom-in-until-when — a
compliance gap for a consent mechanism. The bet: one of the four actions is missing, or the
approved entry lacks `expires_at`.

## Preconditions
- Live stack `:8000`. Fresh run-stamped org `aea33-<stamp>` (provision_company).
- Three grants on the same org to exercise each terminal transition:
  - grant 1: request → approve (yields support.requested + support.approved-with-expires_at).
  - grant 2: request → deny (yields support.requested + support.denied).
  - grant 3: request → revoke (platform-side revoke; yields support.requested + support.revoked).
- Audit read via `GET /platform/orgs/{id}/audit` (platform-gated, metadata only).

## Steps
1. Provision org A. Drive the three lifecycles above (request via platform, approve/deny via the
   org's company_admin, revoke via the platform requester).
2. `GET /platform/orgs/{A}/audit?limit=200`.
3. Collect the set of `action` values; find the `support.approved` entry; read its `details`.

## Expected result
- The action set ⊇ {`support.requested`, `support.approved`, `support.denied`, `support.revoked`}.
- The `support.approved` entry's `details` contains an `expires_at` key with a timestamp (and
  `window_hours` ≈ 4).
- No tenant content appears in any `details` (metadata only).

## Harness
Script: `harness/tc_033.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_033.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 21:55 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The org's audit trail contained all four support actions. The `support.approved` entry's
> `details` carried `expires_at` (ISO timestamp) and `window_hours: 4.0`. No tenant content
> appeared — only grant metadata (reason on the requested events, expiry on approved).

**Evidence**

```
1) provisioned A=802f6069-e849-4205-b27c-e6dea345e4c7
2) grant1 request->approve, grant2 request->deny, grant3 request->revoke driven
3) GET /platform/orgs/{A}/audit -> 200
   actions present: ['support.revoked','support.requested','support.denied','support.requested',
                     'support.approved','support.requested','auth.login.success','org.onboard']
   superset of {requested,approved,denied,revoked}? -> True
4) support.approved details = {'expires_at': '2026-06-01T22:13:11.683980+00:00', 'window_hours': 4.0}
   has expires_at? -> True
PASS all four actions logged; support.approved carries expires_at
```

**Verdict**
The defense held. Each transition records on the same session as its state change:
`PlatformSupportService._record` (platform_support_service.py:107-125) emits
`SUPPORT_REQUESTED`/`SUPPORT_REVOKED`; `CompanySupportService._record`
(company_support_service.py:149-167) emits `SUPPORT_APPROVED`/`SUPPORT_DENIED`. The approve path
(company_support_service.py:84-92) injects `{"expires_at": ..., "window_hours": 4.0}` into the
`details`, so the expiry is recoverable from the trail with no discrete `support.expired` event —
exactly the "logged → expire" decision (EPIC §6). Confirms PC-05-AC8 live.

**Notes / follow-up**
The same-transaction audit write is a deliberate FIX_BEFORE_PROD tradeoff (a failing audit INSERT
would roll back the action) — acceptable here because every `details` field is built from
already-validated inputs. `details` is metadata only (reason + expiry), consistent with the
content-blindness invariant.
