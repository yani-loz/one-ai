# TC-IA-051: Last-admin race — concurrent PATCH→member ×2 strands the org at 0 admins

| Field | Value |
|---|---|
| **ID** | TC-IA-051 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ❌ Fail → ✅ **FIXED** (2026-05-31) |
| **Finding tag** | NEW |

## Objective
Verify the last-admin guard holds under concurrency on the **PATCH / update_user** path: two concurrent
role demotions of the org's only two admins must not be able to drop the active `company_admin` count to
zero.

## Break hypothesis
Same non-atomic check-then-act as TC-IA-050, now via `UserService.update_user` (`user_service.py:81` →
`_guard_last_admin:123`). Two concurrent `PATCH {role:'member'}` requests — one on `admin1`, one on
`admin2` — each exclude *their own* target from `count_active_admins`, both see the peer still active
(count = 1), both pass the guard, both set `role='member'`, both commit → **0 active company_admins**.

## Preconditions
Live stack, persistent DB. Namespace `race-<stamp>-51-<i>`. 50 iterations, each a fresh run-stamped org
with exactly 2 active `company_admin`s before the race. Demo org untouched.

## Steps
1. Onboard fresh org → `admin1`; `admin1` logs in.
2. `admin1` creates `admin2` (company_admin). Assert precondition: 2 active admins (forged-token read).
3. Fire **CONCURRENT** `PATCH /users/{admin1} {role:'member'}` + `PATCH /users/{admin2} {role:'member'}`
   via `asyncio.gather` (only these two calls gathered).
4. Read active-admin count after (forged token + psql).

## Expected result
Atomic guard → exactly one `200` + one `409` (`LastAdminError`); active-admin count never 0.

## Harness
Script: `harness/tc_051.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_051.py`

---

## Execution result

- **Run at:** 2026-05-31 10:48 local
- **Result:** ❌ Fail (the win — guard broke)
- **Finding tag:** NEW

**Actual behavior**

> 48 of 49 firing iterations produced `200+200` (both demotions succeeded) and ended with **0 active
> company_admins**. One iteration produced `200+409` (guard caught the race). One iteration was a setup
> miss (precondition read returned 1 admin, excluded). psql confirmed 0 active admins / 2 total users in
> the firing orgs.

**Evidence**

```
TC-IA-051  run=19e7d34fab8d929  iterations=50
setup failures           : 1
    {'i': 44, 'stage': 'precondition', 'pre_admins': 1, 'org_id': '0fc99a96-234e-49a0-9e16-85bdc28dd166'}
status-pair distribution : {'200+200': 48, '200+409': 1}
ZERO-ADMIN iterations    : 48  (LOCKOUT — the win)
    {'i': 0, 'org_id': '96772761-229f-4ea6-a7f7-4955d93cb80c', 'codes': (200, 200), 'active_admins_after': 0}
    {'i': 1, 'org_id': '9404ef50-d81f-42f8-b7bd-e0b51dbf4a6a', 'codes': (200, 200), 'active_admins_after': 0}
    ...
VERDICT: GUARD BROKE — last-admin PATCH race fired (48/49 firing iterations)

# psql ground truth on firing orgs:
                org_id                | active_admins | total
--------------------------------------+---------------+-------
 9404ef50-d81f-42f8-b7bd-e0b51dbf4a6a |             0 |     2
 96772761-229f-4ea6-a7f7-4955d93cb80c |             0 |     2
```

**Verdict**

Defense **broke** on the PATCH path. 48/49 firing iterations → 0 admins, psql-confirmed. **Severity
Medium** (within-tenant availability/lockout). **Blast radius:** one org per occurrence, no in-app
recovery. **Code path:** `UserService.update_user` → `_guard_last_admin` count at `user_service.py:143`
then the role UPDATE flush at `user_service.py:106`, commit deferred to `get_tenant_session`. Same
non-atomic count→commit window as TC-IA-050.

**Tag = NEW.** The single `200+409` iteration proves the *serial* AUD-07 guard still holds (a
non-overlapping demotion gets 409); the break is the concurrency-safety hole (TOCTOU, CWE-362) the audit
never filed for this path.

**Notes / follow-up**

Same remediation as TC-IA-050 (atomic guard / `FOR UPDATE` / SERIALIZABLE). Generalizes the
non-atomic-guard defect from DELETE to PATCH. See TC-IA-052 for the mixed path.

---

## Remediation (2026-05-31) — ✅ FIXED

The shared `_guard_last_admin` now locks the active-admin set `FOR UPDATE` (ordered by id) before counting, so concurrent `PATCH→member` demotions serialize — the loser re-reads the reduced set and raises `LastAdminError` (409). Same fix as TC-IA-050; the guard is shared by `update_user` and `deactivate_user`.

- **Code:** `UserService._guard_last_admin` → `UserRepository.lock_active_admin_ids` (`FOR UPDATE`).
- **Re-verified live (this harness, 49 iterations):** `status-pair {'200+409': 49}` — **ZERO-ADMIN iterations: 0** (was 48/49).
- **Regression test:** `test_user_service.py::test_last_admin_guard_serializes_concurrent_removals`.
- **Tracked:** DYN-01.
