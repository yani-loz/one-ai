# TC-PC-043: Atomic rollback — duplicate-email abort leaves NO orphan org

| Field | Value |
|---|---|
| **ID** | TC-PC-043 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | ONB — Onboarding contracts + input validation/fuzz |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Onboarding inserts the org first, then the admin user. If the admin email is a duplicate, the user
INSERT fails AFTER the org row was already added to the session — proving atomicity requires that the
**whole transaction rolls back** so no orphan org is committed.

## Break hypothesis
The org row is committed before the user insert fails (or the rollback doesn't cover the org), so a
fresh unique slug `Sb` survives in `organizations` with zero users — a partial-write orphan that
permanently squats the slug and pollutes `GET /platform/orgs`.

## Preconditions
- Live stack; demo platform admin token.
- Run-stamped: slug A `onb43-<stamp>-a`, slug B (`Sb`) `onb43-<stamp>-b`, shared email
  `onb43-<stamp>@oneai.dev`.

## Steps
1. Onboard org A with email E → 201.
2. Onboard org B (fresh unique slug `Sb`) reusing email E → 409.
3. **psql ground-truth:** `SELECT count(*) FROM organizations WHERE slug='<Sb>'` → must be **0**.

## Expected result
Step 2 → 409; step 3 → 0 (orphan org `Sb` does not exist). Slug A still present (sanity).

## Harness
Script: `harness/tc_043.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_043.py | docker compose exec -T backend python -`
psql: `docker compose exec -T db psql -U oneai -d oneai -c "SELECT count(*) FROM organizations WHERE slug='<Sb>';"`

---

## Execution result

- **Run at:** 2026-06-01 08:53 local
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The duplicate-email onboard of fresh slug `Sb` returned 409, and psql confirms `Sb` does NOT exist
> in `organizations` (count=0). No orphan org committed. Slug A (the legit one) is present.
> **Caveat:** in this single-actor sequence the absence of `Sb` is achieved by *never inserting* it
> (the pre-insert email guard short-circuits before the org INSERT), NOT by a rollback removing an
> already-inserted org. See the verdict — the genuine rollback path is concurrency-only.

**Evidence**

```
onboard A (email E): 201
onboard B (fresh slug Sb, reused email E): 409 -> {'detail': 'A user with this email already exists.'}
SLUG_A: onb43-19e8262728847da-a
SLUG_B (must NOT exist in DB): onb43-19e8262728847da-b
```
psql ground-truth:
```
 orphan_count_should_be_0
--------------------------
                        0          <-- Sb does NOT exist
 slug_a_count
--------------
            1                       <-- A persisted (sanity)
all onb43 orgs for this run:
          slug           | status
-------------------------+--------
 onb43-19e8262728847da-a | active   <-- only A, no orphan B
```

**Verdict**

No orphan org — the observable contract holds (psql: `Sb` count = 0). PASS-with-concern because the
test, as specified, does NOT exercise the rollback path it set out to prove:

- In this single-actor sequence the duplicate email is caught by the **pre-insert guard**
  `email_exists` (`platform_auth_service.py:156-157`), which raises `DuplicateUserError` BEFORE the
  org `add` at `:160`. So the org row is **never inserted**. "Sb doesn't exist" is true because
  nothing was ever written — **not** because a rollback removed an already-inserted org.
- The only code that actually rolls back an *already-inserted* org is the **IntegrityError branch**
  (`platform_auth_service.py:178-181`), which is **unreachable single-actor**: it fires only under
  concurrency, when two callers both pass `email_exists`, both INSERT the org, and one then loses the
  `users.email` UNIQUE race. That genuine atomic-rollback proof (AUD-05) belongs to the **RACE suite**
  and is out of ONB scope.

So: the no-orphan invariant is empirically confirmed for the single-actor path, and the pre-insert
guard + the IntegrityError-fallback design (`:163-167`, `:178-181`) are correct by inspection, but
this case does not *independently* prove the rollback fires. Tagged CONFIRMS-FIXED for the AUD-05
409-not-500 behaviour it does confirm (the duplicate is a clean 409), with the efficacy caveat noted.

**Notes / follow-up**

The concurrent variant (two onboards racing on email/slug, both reaching the IntegrityError branch so
a real rollback must remove an inserted org) is the RACE suite's job — that is where atomic rollback
under contention is independently provable via the UNIQUE-violation artifact.
