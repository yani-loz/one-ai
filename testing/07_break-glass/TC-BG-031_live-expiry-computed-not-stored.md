<!--
  Test-case: TC-BG-031. Authored before running; Execution result block written back after.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-031: Live expiry — backdating expires_at flips is_active to false while status stays approved

| Field | Value |
|---|---|
| **ID** | TC-BG-031 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | AEA — Audience confinement + live expiry + audit + input |
| **Type** | Boundary / Adversarial (psql-assisted) |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
PC-05-AC7: a grant's access is decided by the clock, not a stored flag. `is_active` is computed
live as `status=='approved' AND now < expires_at`. An approved grant whose `expires_at` is in the
past must read `is_active=false` while its persisted `status` remains `approved`.

## Break hypothesis
If `is_active` were a stored column (or set once at approval and never re-evaluated), a grant
past its window would still read `is_active=true` — break-glass access would silently outlive its
4-hour box. The bet: forcing `expires_at` into the past via psql leaves `is_active=true` because
the flag is materialized, not computed.

## Preconditions
- Live stack `:8000`. Fresh run-stamped org `aea31-<stamp>` (provision_company).
- A grant requested by the platform admin and approved by the org's company_admin
  (is_active=true, expires_at ~ now+4h).
- psql ground-truth on the **db** container to backdate `expires_at` directly (simulating the
  passage of the 4h window without waiting).

## Steps
1. Provision org A; platform requests a grant on A; company_admin approves → assert
   `is_active=true`, `status=approved`, `expires_at` present.
2. psql: `UPDATE support_grant SET expires_at = now() - interval '1 hour' WHERE id='<grant>';`
3. Re-read via the company inbox `GET /support-access`; locate the same grant.

## Expected result
After backdating, the grant in the inbox reads `is_active=false` while `status` is still
`approved` and `expires_at` is the past timestamp. The clock — not a stored flag — decides.

## Harness
Two-phase (the psql backdate runs on the db container between them):
- `harness/tc_031.py` — phase 1: provision → request → approve, prints `GRANT_ID` + `ADMIN_EMAIL`.
- `harness/tc_031b.py` — phase 2: re-login the same admin, re-read the inbox after the backdate.

Run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_031.py | docker compose exec -T backend python -` → psql UPDATE on the printed grant → `cat …/_common.py …/tc_031b.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 21:45 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Before backdating, the approved grant read `is_active=true`. After the psql UPDATE pushed
> `expires_at` one hour into the past, the SAME grant re-read via the company inbox read
> `is_active=false` while `status` stayed `approved` and `expires_at` showed the past value.
> No write happened on read (status unchanged) — expiry is purely computed.

**Evidence**

```
-- harness phase 1 (before backdate):
1) provisioned A=fbacbec1-7355-45ae-9f60-5ebcd726357e admin_email=admin-aea31-...@oneai.dev
2) approve: 200 status=approved is_active=True expires_at=2026-06-01T22:11:31.992995Z (future)
   GRANT_ID=03326d40-62dd-4574-817a-49f73dc9bd6f

-- psql on db container:
$ docker compose exec -T db psql -U oneai -d oneai -c \
  "UPDATE support_grant SET expires_at = now() - interval '1 hour' WHERE id='03326d40-...';"
UPDATE 1
$ ... -c "SELECT status, expires_at, expires_at < now() AS past FROM support_grant WHERE id='03326d40-...';"
  status  |          expires_at           | past
----------+-------------------------------+------
 approved | 2026-06-01 17:11:40.138715+00 | t

-- harness phase 2 (re-read inbox after backdate):
3) inbox grant: status=approved  is_active=False  expires_at=2026-06-01T17:11:40.138715Z  -> is_active flipped, status unchanged
PASS is_active=false while status stays approved
```

**Verdict**
The defense held. `grant_is_active` (support_grant_view.py:25-30) recomputes
`approved AND now < expires_at` on every read; `to_support_response` (support_grant_view.py:33-47)
stamps the live value. There is no `expired` column and no sweeper — backdating `expires_at`
alone flips `is_active` to false with `status` untouched. Confirms PC-05-AC7 and the EPIC §6
decision ("compute is_active live; no stored expired, no sweeper") under live conditions.

**Notes / follow-up**
The seam note in support_grant_view.py:9-13 is the forward risk: when content endpoints land they
MUST gate on `grant_is_active(...)`, or an expired grant (like this one) would still unlock data
despite reading `is_active=false`. No content endpoint exists today, so this is the documented
forward hook, not a live defect. Directly enables TC-BG-032 (the expired grant is terminal).
