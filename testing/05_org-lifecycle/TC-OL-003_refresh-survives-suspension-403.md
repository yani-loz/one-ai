<!--
  SUSPEND suite — suspend-blocks-login gate (the ⭐ security core). HIGHEST-VALUE case:
  does a suspension-failed refresh leave the session intact, or silently burn it?
-->

# TC-OL-003: Does the refresh token **survive** the suspension 403? (transaction-rollback)

| Field | Value |
|---|---|
| **ID** | TC-OL-003 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | SUSPEND — suspend-blocks-login gate ⭐⭐ |
| **Type** | Adversarial (transaction correctness) |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`AuthService.refresh` calls `token_rotator.consume()` — which stages a `revoke_by_hash` UPDATE on the
session — **before** `_load_loginable_org` raises `OrganizationSuspendedError` (403). Correctness
therefore depends on `get_session` rolling that staged UPDATE back on the exception. This case
observes, on the live stack, whether the pre-suspension refresh token **survives** the 403 (correct:
the staged revoke was rolled back) or is **silently burned** (defect: the session is destroyed even
though the user was only refused for being suspended, and even after the org is reactivated).

## Break hypothesis
If the revoke were committed independently of the request transaction (or `get_session` failed to roll
back on the raised error), the suspension-failed refresh would **consume** the token: after
reactivation, the same refresh token would 401 ("Refresh token is invalid."), having silently revoked
the user's session. That would be a NEW defect — a suspension that, once lifted, leaves the user
locked out of their still-valid refresh token. Correct behaviour: the token row's `revoked_at` stays
NULL through the 403, and the token rotates (200) after reactivation.

## Preconditions
- Live stack up. Fresh run-stamped org via `provision_company(c, plat, "sus003")`; the admin's
  pre-suspension refresh token + its `sha256_hex` storage hash captured.
- A separate single-run probe stops at the 403 (does **not** reactivate) so a psql ground-truth read
  of `refresh_tokens.revoked_at` can observe the rollback directly.

## Steps
1. Provision a fresh company; capture the pre-suspension refresh token and print its hash.
2. Suspend the org. Present the pre-suspension refresh token → expect 403.
3. **psql ground truth (probe run):** `SELECT revoked_at FROM refresh_tokens WHERE token_hash=<hash>`
   → expect NULL (the staged revoke was rolled back).
4. Reactivate the org. Present the **same** refresh token again → observe neutrally:
   - `200` + a **rotated** refresh ⇒ survived (CONFIRMS-FIXED).
   - `401` ⇒ silently burned (NEW DEFECT → escalate).
5. Reactivate is the cleanup (probe org reactivated too).

## Expected result (per the code reading — confirmed neutrally)
- 1st present (suspended): `403`. `revoked_at` IS NULL. 2nd present (reactivated): `200` with a refresh
  token **different** from the one presented (real rotation). The staged revoke was rolled back by
  `get_session` on the `OrganizationSuspendedError`.

## Harness
Script: `harness/tc_003.py` · run: `cat testing/05_org-lifecycle/harness/_common.py testing/05_org-lifecycle/harness/tc_003.py | docker compose exec -T backend python -`
Ground-truth psql keyed on the printed `TOKEN_HASH`:
`docker compose exec -T db psql -U oneai -d oneai -c "SELECT token_hash, revoked_at, (revoked_at IS NULL) AS survived FROM refresh_tokens WHERE token_hash='<hash>';"`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Under suspension the pre-suspension refresh token returned 403. A psql read of its row (taken from a
> probe run that stopped at the 403, before any reactivation) showed `revoked_at IS NULL` — the staged
> `revoke_by_hash` UPDATE was rolled back, not committed. After reactivation the **same** token rotated
> with 200, issuing a new refresh value different from the presented one. The session survived the 403
> intact; the suspension did not burn it.

**Evidence**

```
== TC-OL-003 — does the refresh token SURVIVE the suspension 403? (HIGHEST VALUE) ==
[setup]   org 89b3304e-b442-4dc5-8134-bf9a4f129065
[setup]   TOKEN_HASH=af6b7b018abf4311c2b5176ee12340c6239f64d3121a996a9e2ab8f3bb14f09a
[suspend]  PATCH status=suspended: 200 status=suspended
[403?]     1st present under suspension: 403 body=b'{"detail":"Your organization\'s access is suspended."}'
[react]    PATCH status=active: 200 status=active
[2nd]      same token after reactivation: 200 body=b'{"access_token":"eyJ...","refresh_token":"zIa7LEKt09fGo9z6mhPP2tZ2nsBQNH3dZ0UK7viuKlqeygMJDpFF1AKRdCDSV4es","token_type":"bearer"}'
[rotated?] new refresh differs from old? True
RESULT: 1st=403 2nd=200 -> SURVIVED -> CONFIRMS-FIXED (staged revoke rolled back on the 403)
```

psql ground truth (separate probe run, stopped at the 403 before reactivation):

```
HASH=2d8fbb3696719383af3b288cf94da14c44ca5e5f2762a0b8df4f74f32b454ed8
FORBIDDEN=403

                            token_hash                            | revoked_at | survived
------------------------------------------------------------------+------------+----------
 2d8fbb3696719383af3b288cf94da14c44ca5e5f2762a0b8df4f74f32b454ed8 |            | t
(1 row)
```

**Verdict**

The defense held — both black-box and at the database. `AuthService.refresh` calls
`token_rotator.consume(...)` (`backend/app/identity/services/auth_service.py:96`), which stages a
conditional `UPDATE refresh_tokens SET revoked_at=now() WHERE token_hash=? AND revoked_at IS NULL`
(`token_rotator.py:62`, `refresh_token_repository.py:50-55`) on the request session; only **after**
that does `_load_loginable_org` (`auth_service.py:103`) raise `OrganizationSuspendedError`. The
`get_session` unit-of-work boundary catches the exception and calls `await session.rollback()`
(`backend/app/core/database.py:47-49`), so the staged revoke never commits — `revoked_at` stays NULL
(psql: `survived = t`). The token therefore rotates cleanly (200, new refresh) once the org is
reactivated. The correctness hinges entirely on that rollback, and it holds live. CONFIRMS-FIXED — the
suspension-failed refresh does **not** burn the session.

**Notes / follow-up**
**Now pinned by the unit test `test_refresh_token_survives_suspension_then_rotates_after_reactivation`**
(`backend/tests/identity/routes/test_auth_routes.py`) — a future regression (e.g. from the AUD-06
reuse-family work moving the revoke onto a committed transaction) fails CI, not just this manual pass.
This is the case the static review could not settle (no dynamic/transaction observation). Had the
revoke been emitted on an independent/committed transaction (e.g. once the AUD-06 family-revoke work
lands and needs the revoke to *survive* a 401), this property would regress — re-run this case when
`token_rotator` gains commit-on-reuse behaviour (FIX_BEFORE_PROD "Revoke the refresh-token reuse
family"). Pairs with TC-OL-008 (the gate fully lifts for a fresh login+refresh after reactivation).
**Adversarial flip-side:** survival means suspend→reactivate is a *pause*, not a session reset — a
pre-suspension token resumes on reactivation (no rotation/kill); relevant if an org is ever suspended
*for cause* (see the audit §4).

**Lead re-verification (2026-06-01, after the workflow aborted abnormally).** Re-derived first-hand,
independent of the agent run (`harness/_reverify_003.py`): black-box — suspended present `403` →
reactivate → SAME token present `200` with `rotated=True`; psql on a second token left unconsumed at the
403 → `revoked_at` NULL, `survived=t`. Independently confirms the agent's result. The headline does not
rest on the aborted run.
