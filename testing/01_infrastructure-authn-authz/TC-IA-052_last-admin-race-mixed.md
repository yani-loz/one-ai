# TC-IA-052: Last-admin race — concurrent PATCH→member + DELETE strands the org at 0 admins

| Field | Value |
|---|---|
| **ID** | TC-IA-052 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ❌ Fail → ✅ **FIXED** (2026-05-31) |
| **Finding tag** | NEW |

## Objective
Verify the last-admin guard holds when the two concurrent mutations span **both** code paths at once —
a `PATCH` demotion (`update_user`) and a `DELETE` deactivation (`deactivate_user`) — not just like-with-
like. Proves the bug is in the shared guard, reachable from either entry point simultaneously.

## Break hypothesis
Both `update_user` (`user_service.py:81`) and `deactivate_user` (`user_service.py:109`) call the same
non-atomic `_guard_last_admin`. A concurrent `PATCH {role:'member'}` on `admin1` + `DELETE` on `admin2`
each exclude their own target from `count_active_admins`, both see the peer active (count = 1), both pass,
one demotes + one deactivates, both commit → **0 active company_admins**.

## Preconditions
Live stack, persistent DB. Namespace `race-<stamp>-52-<i>`. 50 iterations, each a fresh run-stamped org
with exactly 2 active `company_admin`s. Demo org untouched.

## Steps
1. Onboard fresh org → `admin1`; `admin1` logs in.
2. `admin1` creates `admin2` (company_admin). Assert precondition: 2 active admins.
3. Fire **CONCURRENT** `PATCH /users/{admin1} {role:'member'}` + `DELETE /users/{admin2}` via
   `asyncio.gather` (positional: [0]=PATCH admin1, [1]=DELETE admin2).
4. Read active-admin count after (forged token + psql).

## Expected result
Atomic guard → exactly one mutation succeeds and the other returns `409`; active-admin count never 0.

## Harness
Script: `harness/tc_052.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_052.py`

---

## Execution result

- **Run at:** 2026-05-31 10:50 local
- **Result:** ❌ Fail (the win — guard broke)
- **Finding tag:** NEW

**Actual behavior**

> 49 of 50 iterations produced `PATCH=200,DELETE=204` (both mutations succeeded across the two paths) and
> ended with **0 active company_admins**. One iteration produced `PATCH=200,DELETE=409` (guard caught the
> race). psql confirmed 0 active admins / 2 total users in the firing orgs.

**Evidence**

```
TC-IA-052  run=19e7d35ece0de64  iterations=50
setup failures           : 0
status-pair distribution : {'PATCH=200,DELETE=409': 1, 'PATCH=200,DELETE=204': 49}
ZERO-ADMIN iterations    : 49  (LOCKOUT — the win)
    {'i': 1, 'org_id': 'bb0759c8-db94-4440-887f-d69651b12911', 'codes': (200, 204), 'active_admins_after': 0}
    {'i': 2, 'org_id': '16f1e85e-c834-4836-a1da-dd27855a6576', 'codes': (200, 204), 'active_admins_after': 0}
    ...
VERDICT: GUARD BROKE — mixed PATCH+DELETE race fired (49/50 firing iterations)

# psql ground truth on firing orgs:
                org_id                | active_admins | total
--------------------------------------+---------------+-------
 16f1e85e-c834-4836-a1da-dd27855a6576 |             0 |     2
 bb0759c8-db94-4440-887f-d69651b12911 |             0 |     2
```

**Verdict**

Defense **broke** across both mutating paths simultaneously. 49/50 firing iterations → 0 admins,
psql-confirmed. **Severity Medium** (within-tenant availability/lockout). **Blast radius:** one org per
occurrence, no in-app recovery. **Code path:** the shared `_guard_last_admin` (`user_service.py:123`)
invoked from `update_user:97` and `deactivate_user:119`; both count then flush before either commits.

**Tag = NEW.** The single `DELETE=409` iteration proves the serial AUD-07 guard holds; the break is the
TOCTOU concurrency hole (CWE-362). Mixed-path firing shows the defect is the guard's non-atomicity, not a
quirk of one endpoint.

**Notes / follow-up**

Same remediation as TC-IA-050/051. The mixed-path result means a fix must serialize the admin-count
invariant across **all** mutating entry points (create/update/deactivate), not just within one handler.
Recommend a single atomic check at the repository/DB layer (`FOR UPDATE` or conditional `UPDATE`).

---

## Remediation (2026-05-31) — ✅ FIXED

Done exactly as recommended: the single shared `_guard_last_admin` locks the active-admin set `FOR UPDATE` at the repository layer, so the invariant is enforced across **both** mutating paths (`update_user` PATCH + `deactivate_user` DELETE) at once. Whichever transaction wins the lock proceeds; the other re-reads the reduced set and 409s.

- **Code:** `UserService._guard_last_admin` → `UserRepository.lock_active_admin_ids` (`FOR UPDATE`).
- **Re-verified live (this harness, 50 iterations):** `status-pair {'PATCH=200,DELETE=409': 18, 'PATCH=409,DELETE=204': 32}` — **ZERO-ADMIN iterations: 0** (was 49/50). Either path can win the race; exactly one mutation succeeds.
- **Regression test:** `test_user_service.py::test_last_admin_guard_serializes_concurrent_removals`.
- **Tracked:** DYN-01.
