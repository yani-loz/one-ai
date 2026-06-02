# TC-BG-024: Concurrent approve + revoke on one grant — no lost update (row lock), 50 iters

| Field | Value |
|---|---|
| **ID** | TC-BG-024 · **Suite** STATE · **Type** Concurrency · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
50 grants, each hit by concurrent approve+revoke:
APPROVE_TALLY {200:25, 409:25} | REVOKE_TALLY {200:50} | ANY_5XX False
INBOX_STATUS_TALLY {'revoked':50} active=0 | psql GROUP BY: status=revoked count=50 (no approved row)
```
**Verdict:** Defense held under real concurrency — the lost-update/TOCTOU (PR-5 review #1) does not reproduce.
The ordering-independent invariant held: **all 50 ended `revoked`, 0 approved/active** (psql-confirmed), no
5xx. Serialization via `get_in_org().with_for_update()` (`support_grant_repository.py:67`) + the service
guards. ("Both 200" for approve-then-revoke is benign approve-first ordering; the real discriminator is final
status==revoked.) CORROBORATES the `FOR UPDATE` fix (same-row lock by design), not independent proof. PC-05-AC4b.
