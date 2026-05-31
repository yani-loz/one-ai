# TC-IA-054: Create-user duplicate race — concurrent same-email POST /users yields 201+409, never 500

| Field | Value |
|---|---|
| **ID** | TC-IA-054 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the AUD-05 fix on the `POST /users` path: a concurrent same-email create race resolves to one
`201` + one `409` (`DuplicateUserError`), and **never** a `500`. The `users.email` UNIQUE constraint plus
the `IntegrityError`→`409` translation must backstop the check-then-insert TOCTOU.

## Break hypothesis
`create_user` is check-then-act: `email_exists` pre-check (`user_service.py:63`) then `add`/flush
(`:74`). Two concurrent identical-email creates both pass the pre-check; the second `INSERT` raises
SQLAlchemy `IntegrityError`. Pre-AUD-05 that propagated as an unhandled **HTTP 500**. The fix catches it
(`user_service.py:75-78`) and re-raises `DuplicateUserError` → 409. Any `500` in the race → REFUTES-FIX.

## Preconditions
Live stack, persistent DB. Namespace `race-<stamp>-54-<t>`. 50 trials; each trial a fresh run-stamped
org/admin. Demo org untouched.

## Steps
1. Per trial: onboard fresh org + admin; admin logs in.
2. Fire **2 CONCURRENT** `POST /users` with the SAME new email (`role=member`) via `asyncio.gather`.
3. Record the status pair; flag any `500` or any pair ≠ `{201,409}`.
4. After the run, grep the **db** log for `users_email_key` violations to prove the INSERT collision
   (the `IntegrityError` branch) actually fired — distinguishing it from the serial pre-check (both yield
   409).

## Expected result
Every trial: `201+409`, no `500`. The db log shows duplicate-key violations on `users_email_key`,
proving the race reached the INSERT and the `IntegrityError`→409 conversion executed.

## Harness
Script: `harness/tc_054.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_054.py`

---

## Execution result

- **Run at:** 2026-05-31 10:54 local
- **Result:** ✅ Pass (defense held)
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> All 50 race trials produced `201+409`. Zero `500`s, zero non-`{201,409}` pairs. The db log recorded
> 47 `duplicate key value violates unique constraint "users_email_key"` errors during the run — proving
> the loser reached the INSERT and the `IntegrityError`→`DuplicateUserError`→409 branch executed under
> real contention (not merely the serial `email_exists` pre-check).

**Evidence**

```
TC-IA-054  run=19e7d38993c64e5  trials=50  (2 concurrent POST /users, same email)
setup failures        : 0
status-pair dist      : {'201+409': 50}
bad trials (500 / !=  {201,409}) : 0
VERDICT: CONFIRMS_FIXED — every race trial = 201+409, no 500 (50/50)

# db log during the run (proof the INSERT collision / IntegrityError branch fired):
db-1  ... ERROR:  duplicate key value violates unique constraint "users_email_key"   (×47)
```

**Verdict**

Defense **held** — AUD-05 is empirically confirmed fixed on the `POST /users` path. The 47 db-level
`users_email_key` violations prove the race genuinely engaged the INSERT (the loser passed the pre-check
and hit the constraint), and the app translated every one to a clean 409 — never a 500. **Code path:**
`UserService.create_user` catch at `user_service.py:75-78`, mapping `IntegrityError` →
`DuplicateUserError` (→ 409 via `error_handlers.py`). The DB UNIQUE constraint is the true backstop; the
catch makes the response correct.

**Notes / follow-up**

No follow-up. AUD-04 (cross-tenant email-existence oracle — the *serial* 409-vs-201 disclosure) is a
distinct, documented deferral and is out of scope for this race case.
