# TC-IA-055: Onboarding integrity race — concurrent slug/email collisions yield 409, no orphan org

| Field | Value |
|---|---|
| **ID** | TC-IA-055 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the AUD-05 fix and the atomic-onboarding invariant on `POST /platform/orgs`:
(a) a concurrent same-slug onboard race yields one `201` + one `409` and exactly **one** committed org;
(b) an admin email that already exists is rejected with `409` and commits **no orphan org** — including
the concurrent variant that drives both org INSERTs through and forces the loser's whole transaction
(org + admin) to roll back (the audit's "untested" `IntegrityError` branch at
`platform_auth_service.py:160`).

## Break hypothesis
`onboard_organization` is check-then-act on both slug (`get_by_slug`, `:136`) and admin email
(`email_exists`, `:138`), then inserts org (`:142`) then admin (`:151`) in one transaction. Concurrency
races the pre-checks: the slug collision must resolve to 409 with one org (`IntegrityError` →
`DuplicateOrganizationError`, `:145`); the email collision — when two different new slugs share one new
email — drives **both** org INSERTs through, then collides on `users.email`, so the loser's
`IntegrityError` (`:160`) must roll back its already-inserted org → no orphan. A `500`, two orgs for one
slug, or an orphan org → NEW/REFUTES-FIX.

## Preconditions
Live stack, persistent DB. Namespace `race-<stamp>-55a/55b1/55b2`. Demo org untouched.
- (a) 30 trials, concurrent same-slug onboards (different admin emails).
- (b1) literal: seed an admin email, then onboard a new slug reusing it (serial).
- (b2) 20 trials, concurrent onboards with **different** new slugs but the **same** brand-new admin email.

## Steps
1. **(a)** For 30 trials: fire 2 concurrent `POST /platform/orgs` with the same slug; after each, count
   committed orgs with that slug via `GET /platform/orgs`.
2. **(b1)** Onboard a seed org with email E; then onboard a new slug with email E → expect 409, slug count 0.
3. **(b2)** For 20 trials: fire 2 concurrent `POST /platform/orgs`, different new slugs, same new email;
   expect one 201 + one 409 and exactly **one** of the two slugs committed (loser's org rolled back).
4. Cross-check orphans via psql; grep the db log for `users_email_key` violations to prove (b2) reached
   the user INSERT (the `IntegrityError` rollback branch), not the email pre-check.

## Expected result
(a) 30×`201+409`, exactly 1 org per slug. (b1) 409, 0 orphan orgs. (b2) 20×`201+409`, exactly 1 org
committed per trial, 0 orphans; db log shows 20 `users_email_key` violations (one loser INSERT per trial).

## Harness
Script: `harness/tc_055.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_055.py`

---

## Execution result

- **Run at:** 2026-05-31 10:55 local
- **Result:** ✅ Pass (defense held)
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> (a) 30/30 trials = `201+409`, exactly one org committed per slug, zero `500`s. (b1) the dup-email
> onboard returned `409` with the documented body and zero orphan orgs for the new slug. (b2) 20/20
> trials = `201+409` with exactly one slug committed per trial (loser's org absent); zero orphan/anomaly
> trials. psql confirmed 0 orphan orgs and exactly 20 b2 orgs committed. The db log recorded **20
> `users_email_key` violations** in the b2 window (one per trial) — proving each loser reached the user
> INSERT and the `IntegrityError` rollback branch (`:160`) executed, rolling its already-inserted org
> back. The (a) window recorded **30 `organizations_slug_key` violations** (one per trial) — proving the
> slug race reached the org INSERT and the `:145` branch fired.

**Evidence**

```
TC-IA-055  run=19e7d3e9f5757b6
(a) same-slug concurrent  trials=30
    status-pair dist : {'201+409': 30}
    bad trials (500 / wrong pair / != 1 org) : 0
(b1) literal new-slug + EXISTING email
    dup-email onboard code : 409   body: {"detail":"A user with this email already exists."}
    orphan orgs with new slug : 0
(b2) concurrent DIFFERENT slugs + SAME new email  trials=20
    status-pair dist : {'201+409': 20}
    sample : {'t':0,'codes':[409,201],'slug0_orgs':0,'slug1_orgs':1}  {'t':1,'codes':[201,409],'slug0_orgs':1,'slug1_orgs':0} ...
    ORPHAN/anomaly trials (committed_orgs != 1, 500, or wrong pair) : 0
VERDICT: CONFIRMS_FIXED — (a) 1 org/slug + 201+409, (b1) 409 no orphan, (b2) IntegrityError branch rolls back loser org (no orphan)

# db log attributable to this run (proof the INSERT-level branches fired, not the pre-checks):
users_email_key violations since boundary        : 20   (b2: one loser user-INSERT per trial)
organizations_slug_key violations since boundary : 30   (a: one loser org-INSERT per trial)

# psql ground truth:
orphan orgs (org row with 0 users) for 55b2 slugs : 0 rows
total 55b2 orgs committed                          : 20   (one winner per trial)
```

**Verdict**

Defense **held** — AUD-05 confirmed fixed on the onboarding path, and the audit's "untested"
`IntegrityError` rollback branch (`platform_auth_service.py:160`) is now empirically exercised. The 20
attributable `users_email_key` violations prove (b2) reached the user INSERT (the branch the serial
pre-check never reaches), and `committed_orgs==1` + zero psql orphans prove the loser's org rolled back
atomically. The 30 `organizations_slug_key` violations likewise prove (a) engaged the slug INSERT
(`:145`). No `500`, no orphan, no double-commit anywhere.

**Positive control:** the same `asyncio.gather`/pool that fired TC-IA-050/051/052 (48–49/50) drove these
races; the db-log violation counts are independent confirmation that the contended INSERTs actually
collided. So the held defense is not a serialization artifact — it held under contention proven real both
by the broken sibling cases and by the constraint-violation log.

**Notes / follow-up**

No follow-up. (b2) closes the audit-flagged gap that `onboard_organization`'s two `IntegrityError`
branches were "fixed-by-symmetry but not independently exercised."
