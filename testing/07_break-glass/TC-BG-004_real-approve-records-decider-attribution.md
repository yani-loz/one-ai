<!--
  Test-case template. Copy this file to testing/<NN>_<target>/TC-<TT>-<NNN>_<slug>.md
  and fill every section. Author the top half BEFORE running; write the
  "Execution result" block back into this same file AFTER running.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-004: A real company approve records decider attribution + the time box

| Field | Value |
|---|---|
| **ID** | TC-BG-004 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | CONSENT — approval path + forged-token blast radius |
| **Type** | Positive |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
PC-05-AC2 (decider attribution): after a REAL company_admin approve, the grant records
`decided_by_email` (WHO consented) + `decided_at` (WHEN) + `expires_at` (UNTIL when, now+4h),
and `is_active=true`. This is the deliberate contrast with TC-BG-003's forged token, which
produced a phantom (null) decider.

## Break hypothesis
The approve path forgets to stamp the decider — `decided_by_email`/`decided_at` come back null,
or `expires_at` isn't set — so an approved grant cannot prove WHO consented or for HOW LONG,
breaking the accountability promise of consented access.

## Preconditions
- Live stack `:8000`. Real company_admin token from `provision_company` (`org["admin_access"]`,
  email `admin-<slug>@oneai.dev`) — the genuine consenting actor.
- Run-stamped namespace: `provision_company(prefix="consent-bg004")`.

## Steps
1. Platform login; `provision_company` a fresh org (captures the real admin token + email).
2. `request_support` → grant id, status `requested`.
3. `company_approve(org["admin_access"], grant)` with the REAL company_admin token.
4. Capture the approve body; psql ground-truth the persisted row.

## Expected result
- `POST /support-access/{grant}/approve` (real admin) → `200`, `status='approved'`,
  `is_active=true`.
- `decided_by_email` = `admin-<slug>@oneai.dev` (the real consenting admin).
- `decided_at` non-null; `expires_at` ≈ `decided_at + 4h` (the time box).
- psql: row shows `status='approved'`, real `decided_by_email`, `decided_by_user_id` =
  the real admin's user id, `expires_at` set.

## Harness
Script: `harness/tc_004.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_004.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 18:13 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> A real company_admin approve returned `200`, `status='approved'`, `is_active=true`, with
> `decided_by_email=admin-<slug>@oneai.dev`, `decided_at` set, and `expires_at = decided_at+4.0h`.
> psql confirmed the persisted row carried the real decider email + a real user id + the time box.

**Evidence**

```
== ORG == 34d1971b-aa81-4b90-ad68-ce086fee5ef0 consent-bg004-19e84646a57cf00
== REAL admin email == admin-consent-bg004-19e84646a57cf00@oneai.dev
== REQUEST == 201 grant f094f722-28b9-4274-adb2-988f464b41b7 status requested
== REAL approve status == 200
status      : approved
is_active   : True
decided_at  : 2026-06-01T18:13:54.543609Z
decided_by  : admin-consent-bg004-19e84646a57cf00@oneai.dev
expires_at  : 2026-06-01T22:13:54.543609Z
== window hours (expires_at - decided_at) == 4.0
== FULL BODY == {'id': 'f094f722-28b9-4274-adb2-988f464b41b7', 'org_id': '34d1971b-aa81-4b90-ad68-ce086fee5ef0', 'requested_by_admin_id': '21760a63-7466-458d-b60b-01bf49c88c44', 'requested_by_email': 'super@ethera.ai', 'reason': 'break-glass: incident investigation', 'status': 'approved', 'is_active': True, 'decided_at': '2026-06-01T18:13:54.543609Z', 'decided_by_email': 'admin-consent-bg004-19e84646a57cf00@oneai.dev', 'expires_at': '2026-06-01T22:13:54.543609Z', 'created_at': '2026-06-01T18:13:54.530049Z'}

-- psql ground-truth (db container) --
SELECT status, decided_by_email, decided_by_user_id IS NOT NULL AS has_user_id, expires_at
  FROM support_grant WHERE id = 'f094f722-28b9-4274-adb2-988f464b41b7';

  status  |               decided_by_email                | has_user_id |          expires_at
----------+-----------------------------------------------+-------------+-------------------------------
 approved | admin-consent-bg004-19e84646a57cf00@oneai.dev | t           | 2026-06-01 22:13:54.543609+00
(1 row)
```

**Verdict**

A real approve fully attributes the consent: WHO (`decided_by_email` + `decided_by_user_id`,
stamped at `company_support_service.py:140-147` `_stamp_decision`), WHEN (`decided_at`), and
UNTIL (`expires_at = now + SUPPORT_ACCESS_WINDOW`, `company_support_service.py:80-83`). The
4h window matches `SUPPORT_ACCESS_WINDOW`. Defense held. (Pure positive/contract test; tag —.)
The contrast with TC-BG-003 is the point: a REAL token yields real attribution; the forged
token yields a phantom (null) decider — accountability degrades exactly with the forgery.

**Notes / follow-up**

`expires_at` drives the live-expiry check (`support_grant_view.grant_is_active`); the time-box
expiry behavior is the AUDIENCE+EXPIRY+AUDIT suite's territory. `support.approved` carries
`expires_at` in its audit details (PC-05-AC8).
