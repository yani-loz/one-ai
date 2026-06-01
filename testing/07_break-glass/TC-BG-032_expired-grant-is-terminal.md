<!--
  Test-case: TC-BG-032. Authored before running; Execution result block written back after.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-032: Expiry is terminal — an expired (approved-but-past) grant cannot be re-approved (409)

| Field | Value |
|---|---|
| **ID** | TC-BG-032 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | AEA — Audience confinement + live expiry + audit + input |
| **Type** | Negative / Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
An expired window cannot be reopened by re-approving. Because `status` stays `approved` after
expiry (only the clock moved), a second `approve` must be rejected by the state guard — approve
requires the grant to be `requested`, and an `approved` (even expired) grant is not. So there is
no path to resurrect an expired grant's access by re-approval.

## Break hypothesis
If approval were idempotent on the *active* flag rather than guarded on `status`, an admin could
re-approve an expired grant and `expires_at` would be re-stamped to now+4h — silently resurrecting
a window that consent had already let lapse. The bet: approve on the expired grant returns 200 and
re-opens the box.

## Preconditions
- Continues from TC-BG-031's state model: a grant that is `status=approved` with a past
  `expires_at` (is_active=false). The harness re-creates that state self-contained: request →
  approve → psql-backdate `expires_at` → confirm is_active=false → then attempt re-approve.

## Steps
1. Provision org A; request + approve a grant (is_active=true).
2. psql backdate `expires_at` to `now() - interval '1 hour'` (grant now expired, status approved).
3. Confirm via inbox: status=approved, is_active=false.
4. company_admin `POST /support-access/{grant}/approve` again.

## Expected result
The re-approve returns **409** (`InvalidGrantTransitionError` — "Cannot decide a grant that is
already approved"). The grant's `expires_at` is NOT re-stamped; status stays `approved`,
is_active stays false. The expired window stays closed.

## Harness
Two-phase (the psql backdate runs on the db container between them):
- `harness/tc_032.py` — phase 1: provision → request → approve, prints `GRANT_ID` + `ADMIN_EMAIL`.
- `harness/tc_032b.py` — phase 2: re-login the same admin, confirm expired, attempt re-approve.

Run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_032.py | docker compose exec -T backend python -` → psql UPDATE on the printed grant → `cat …/_common.py …/tc_032b.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 21:50 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> After backdating the approved grant into the past (is_active=false), the company_admin's
> re-approve was rejected with 409 and the message that the grant is already `approved`. The
> grant's `expires_at` was unchanged (still the past value) — the window was not resurrected.

**Evidence**

```
1) provisioned A=74477af6-b529-46bd-9d78-2daec0ecb628; grant=65261f13-4b7e-47bf-8e44-b33b08d9e6d6
2) approve: 200 status=approved is_active=True expires_at=2026-06-01T22:12:19.812539Z (future)
3) psql: UPDATE support_grant SET expires_at = now() - interval '1 hour' WHERE id='65261f13-...';  -> UPDATE 1
4) inbox after backdate: status=approved is_active=False expires_at=2026-06-01T17:12:25.518193Z
5) re-approve expired grant: 409  body={"detail":"Cannot decide a grant that is already approved."}
6) inbox after rejected re-approve: status=approved is_active=False expires_at=2026-06-01T17:12:25.518193Z (UNCHANGED)
PASS expired grant is terminal — 409, expires_at not re-stamped
```

**Verdict**
The defense held. The approve path loads via `_load_requested` (company_support_service.py:129-138)
which requires `status == requested`; an expired-but-`approved` grant fails that guard →
`InvalidGrantTransitionError` → 409 (error_handlers). The guard is status-based, not active-flag
based, so expiry is genuinely terminal: there is no re-approve path that re-stamps `expires_at`.
Confirms PC-05-AC4 (state machine 409 matrix) for the specific adversarial case of an *expired*
approved grant. Composes with TC-BG-031 (live expiry) to prove the window neither auto-renews nor
can be manually reopened.

**Notes / follow-up**
To genuinely reopen access, the platform admin must file a NEW request (new `requested` grant) and
the company_admin must approve it again — a fresh consent event, fully logged. That is the intended
re-grant path, not re-approval of a lapsed grant.
