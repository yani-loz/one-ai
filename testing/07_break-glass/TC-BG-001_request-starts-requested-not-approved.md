<!--
  Test-case template. Copy this file to testing/<NN>_<target>/TC-<TT>-<NNN>_<slug>.md
  and fill every section. Author the top half BEFORE running; write the
  "Execution result" block back into this same file AFTER running.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-001: A break-glass request starts `requested` (never `approved`) and records the requester email

| Field | Value |
|---|---|
| **ID** | TC-BG-001 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | CONSENT — approval path + forged-token blast radius |
| **Type** | Positive |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
PC-05-AC1: a platform admin's `POST /platform/orgs/{id}/support-requests` opens a grant in
status `requested` — never `approved` — with `is_active=false`, no time box, and the
**denormalized requester email** persisted (informed consent: the company sees WHO is asking).

## Break hypothesis
The request endpoint silently auto-approves (sets `status='approved'` / `expires_at` /
`is_active=true`) or fails to record `requested_by_email`, so a grant could confer access
before any customer consent, or the consent record can't name the requester.

## Preconditions
- Live stack `:8000`, harness inside the backend container over stdin.
- Run-stamped namespace: a fresh org provisioned via `provision_company(prefix="consent-bg001")`
  (slug `consent-bg001-<stamp>`, lowercase). Demo platform admin (`super@ethera.ai`) only
  REQUESTS — never mutated as a target.

## Steps
1. Platform login (demo admin).
2. `provision_company` a fresh run-stamped org.
3. `request_support(plat_token, org_id)` → capture status + body.
4. psql ground-truth: read the persisted `support_grant` row (status, expires_at,
   decided_*, requested_by_email). `is_active` is COMPUTED (not a column) — confirm the
   stored fields that drive it.

## Expected result
- HTTP `201`.
- Body: `status='requested'`, `is_active=false`, `expires_at=null`, `decided_at=null`,
  `decided_by_email=null`, `requested_by_email='super@ethera.ai'`, `reason` echoed.
- psql row: `status='requested'`, `expires_at IS NULL`, `decided_by_email IS NULL`,
  `requested_by_email='super@ethera.ai'`.

## Harness
Script: `harness/tc_001.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_001.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 18:12 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> The request returned `201` with `status='requested'`, `is_active=false`, `expires_at=null`,
> `decided_at=null`, `decided_by_email=null`, and `requested_by_email='super@ethera.ai'`. The
> psql ground-truth row confirmed the stored fields: `status=requested`, `expires_at` NULL,
> `decided_by_email` NULL, `requested_by_email=super@ethera.ai`. A request never starts approved.

**Evidence**

```
== ORG == d63a7630-8a75-4552-b4dc-c8f34c200ec0 consent-bg001-19e846350e8f161
== REQUEST status == 201
status      : requested
is_active   : False
expires_at  : None
decided_at  : None
decided_by  : None
requested_by: super@ethera.ai
reason      : break-glass: incident investigation
grant_id    : 1961d075-3812-4d85-a430-5a721c753fca
== FULL BODY == {'id': '1961d075-3812-4d85-a430-5a721c753fca', 'org_id': 'd63a7630-8a75-4552-b4dc-c8f34c200ec0', 'requested_by_admin_id': '21760a63-7466-458d-b60b-01bf49c88c44', 'requested_by_email': 'super@ethera.ai', 'reason': 'break-glass: incident investigation', 'status': 'requested', 'is_active': False, 'decided_at': None, 'decided_by_email': None, 'expires_at': None, 'created_at': '2026-06-01T18:12:42.483114Z'}

-- psql ground-truth (db container) --
SELECT status, expires_at, decided_at, decided_by_email, requested_by_email
  FROM support_grant WHERE id = '1961d075-3812-4d85-a430-5a721c753fca';

  status   | expires_at | decided_at | decided_by_email | requested_by_email
-----------+------------+------------+------------------+--------------------
 requested |            |            |                  | super@ethera.ai
(1 row)
```

**Verdict**

A request is opened strictly in the pending `requested` state with no time box and
`is_active=false` — consent is structurally pending. The requester email is captured at
write time (`platform_support_service.py:71-79`, `request_access`), so the consent record is
attributable. Defense held. (Pure positive/contract test — no prior audit fix maps to it; tag —.)

**Notes / follow-up**

Feeds TC-BG-004 (decider attribution after a real approve) and TC-BG-002 (the only approve
path is company-side). `is_active` is computed live in `support_grant_view.grant_is_active`.
