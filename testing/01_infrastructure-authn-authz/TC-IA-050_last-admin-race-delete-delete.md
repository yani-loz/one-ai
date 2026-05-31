# TC-IA-050: Last-admin race — concurrent DELETE + DELETE strands the org at 0 admins

| Field | Value |
|---|---|
| **ID** | TC-IA-050 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ❌ Fail → ✅ **FIXED** (2026-05-31) |
| **Finding tag** | NEW |

## Objective
The last-admin guard (`UserService._guard_last_admin`, `user_service.py:123`) must guarantee an
org can never be locked out of its own user management: no action may drop the org's active
`company_admin` count to zero. This verifies that invariant holds **under concurrency**, not just
serially.

## Break hypothesis
`_guard_last_admin` is a non-atomic check-then-act: it `SELECT`s `count_active_admins(exclude=target)`
(`user_repository.py:81`) then mutates + flushes, with the commit deferred to the request's
`get_tenant_session` (`dependencies.py:120`) — **no `FOR UPDATE` row lock, no serializable isolation**.
Two concurrent `DELETE /users/{id}` requests, each excluding *its own* target from the count, both see
the *other* admin still active (count = 1, not 0), both pass the guard, both deactivate, both commit →
**0 active admins = org locked out** with no in-app recovery path. The AUD-07 fix closed the *serial*
lockout; this reopens it on the concurrent path the audit's Limitations section says was never tested.

## Preconditions
Live stack (`http://localhost:8000`), persistent DB. Namespace `race-<stamp>-50-<i>`. Each iteration
provisions a **fresh** run-stamped org via the platform admin (`super@ethera.ai`); the demo org is never
touched. 50 iterations, each a fresh org with exactly 2 active `company_admin`s before the race.

## Steps
1. Platform admin onboards a fresh org → `admin1` (company_admin) + `org_id`.
2. `admin1` logs in → real company_admin access token.
3. `admin1` creates `admin2` (role=`company_admin`) via `POST /users` → org now has 2 active admins.
4. Assert the precondition (forged company_admin token + `GET /users` → exactly 2 active admins).
5. Fire **CONCURRENT** `DELETE /users/{admin1}` + `DELETE /users/{admin2}` via `asyncio.gather`, both
   with `admin1`'s token. Only these two calls are gathered (all setup precedes the gather).
6. Read active-admin count after, via forged token `GET /users` and via psql ground truth.

## Expected result
A correct atomic guard makes **exactly one** DELETE succeed (204) and the other return **409**
(`LastAdminError`), leaving **≥1 active admin**. The status-pair should be `204+409` every iteration and
the post-race active-admin count should never be 0.

## Harness
Script: `harness/tc_050.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_050.py`

---

## Execution result

- **Run at:** 2026-05-31 10:46 local
- **Result:** ❌ Fail (the win — guard broke)
- **Finding tag:** NEW

**Actual behavior**

> 48 of 50 iterations produced `204+204` (both deletes succeeded) and ended with **0 active
> company_admins** — the org is locked out of its own user management. Only 2 iterations produced
> `204+409` (the guard caught the race). The status-pair distribution correlates perfectly with the
> outcome: every `204+204` → 0 admins; every `204+409` → ≥1 admin. psql confirmed 0 active admins / 2
> total users (both soft-deleted) in the firing orgs.

**Evidence**

```
TC-IA-050  run=19e7d326a5bde19  iterations=50
setup failures           : 0
status-pair distribution : {'204+409': 2, '204+204': 48}
ZERO-ADMIN iterations    : 48  (LOCKOUT — the win)
    {'i': 1, 'org_id': 'dce129c9-9347-493a-b102-890a44130318', 'codes': (204, 204), 'active_admins_after': 0}
    {'i': 2, 'org_id': '05178919-6c9b-45e8-9fb6-0788fda98d30', 'codes': (204, 204), 'active_admins_after': 0}
    ...
VERDICT: GUARD BROKE — last-admin race fired (48/50 firing iterations)

# psql ground truth on firing orgs:
                org_id                | active_admins | total_users
--------------------------------------+---------------+-------------
 05178919-6c9b-45e8-9fb6-0788fda98d30 |             0 |           2
 0bcefd6a-5b30-4a38-b27b-897038632505 |             0 |           2
 dce129c9-9347-493a-b102-890a44130318 |             0 |           2
```

**Verdict**

Defense **broke**. The `204+204 → 0 admins` outcome reproduced in 48/50 iterations and is confirmed by
psql. **Severity Medium** — within-tenant availability/lockout (no cross-tenant breach). **Blast radius:**
a single org per occurrence; an org whose two admins are deleted concurrently has **no in-app recovery**
(members are 403 on `/users`; a self-deactivated admin can't log back in), recoverable only by platform
admin or direct DB intervention. **Code path:** the non-atomic guard — `UserService._guard_last_admin`
counts at `user_service.py:143` (via `UserRepository.count_active_admins`, `user_repository.py:81`,
plain `SELECT count`), then `deactivate_user` flushes the `is_active=False` UPDATE at
`user_service.py:121`, with the commit deferred to `get_tenant_session` (`dependencies.py:134-141`). No
`FOR UPDATE` / serializable isolation closes the count→commit window.

**Tag = NEW (not REFUTES_FIX).** AUD-07's claim — and its 2026-05-31 "FIXED" verification (a *serial*
live smoke: last-admin → 409) — is that a single sequential action cannot drop admins to zero. The
`204+409` iterations here are direct evidence that the **serial guard still holds**: when the requests
don't overlap, exactly one gets 409. What broke is the guard's **concurrency-safety** — a TOCTOU
(CWE-362/367), a different defect class from AUD-07's CWE-841 workflow bug. The audit never claimed
concurrency-safety; its Limitations section explicitly disclaims it ("no concurrency load test to observe
the races firing"), and it files concurrency races as their own findings (AUD-01, AUD-05). This is the
last-admin analogue the audit never filed → genuinely NEW.

**Notes / follow-up**

Remediation: make the guard atomic — `SELECT ... FOR UPDATE` on the candidate admins, or a single
conditional `UPDATE ... WHERE` that asserts another active admin still exists, or SERIALIZABLE isolation
on the mutating transaction; treat the lost race as `LastAdminError` (409). Same root cause as the
AUD-01 rotation race (which *was* fixed with a conditional `UPDATE ... WHERE revoked_at IS NULL` —
TC-IA-053), so the fix pattern is already in the codebase. Generalizes to TC-IA-051 (PATCH path) and
TC-IA-052 (mixed path). Recommend adding to `docs/FIX_BEFORE_PROD.md`.

---

## Remediation (2026-05-31) — ✅ FIXED

`_guard_last_admin` now **locks the active-admin set `FOR UPDATE`** (ordered by id so concurrent guards queue instead of deadlocking) before counting, so two simultaneous removals serialize: the loser re-reads the now-smaller set and raises `LastAdminError` (409). Same atomic pattern AUD-01 used for the rotation race.

- **Code:** `UserService._guard_last_admin` (`backend/app/identity/services/user_service.py`) → `UserRepository.lock_active_admin_ids` (`user_repository.py` — `SELECT id … WHERE role='company_admin' AND is_active ORDER BY id FOR UPDATE`). `count_active_admins` removed.
- **Re-verified live (this harness, 50 iterations):** `status-pair {'204+409': 50}` — **ZERO-ADMIN iterations: 0** (was 48/50).
- **Regression test:** `tests/identity/services/test_user_service.py::test_last_admin_guard_serializes_concurrent_removals` (two real concurrent transactions → exactly one wins).
- **Tracked:** DYN-01 in `docs/audits/2026-05-31_identity-dynamic-adversarial.md`.
