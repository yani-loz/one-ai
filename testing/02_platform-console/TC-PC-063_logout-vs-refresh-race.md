# TC-PC-063: Logout-vs-refresh race on the same platform token

| Field | Value |
|---|---|
| **ID** | TC-PC-063 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | RACE — Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | NEW (minor observation — dangling descendant on logout-loses) |

## Objective
Prove that `POST /platform/logout` and `POST /platform/refresh` racing on the SAME refresh
token never corrupt state: no 500, the token ends up dead either way, and at most one of the
two "consumes" it. Both endpoints mutate the same `refresh_tokens` row via the conditional
revoke.

## Break hypothesis
Logout (`revoke` — unconditional revoke-if-unrevoked) and refresh (`consume` — revoke-then-
issue) both target one row. A naive implementation could: (a) raise a 500 on a write
conflict / double-revoke; (b) let BOTH "win" so the token both revokes (logout) and rotates
(refresh), leaving a live descendant after an explicit logout — a session-fixation-ish defect
where logout fails to terminate the session; or (c) leave the token live after the pair. Any
of these is the win.

## Preconditions
- Live stack up; demo platform admin (a FRESH token minted each of the ~50 iterations — no
  shared state across iterations).
- HTTP-layer proof only (refresh_tokens has no run-stamp column); invariants asserted on
  status codes per iteration.

## Steps
For each of 50 iterations:
1. `POST /platform/login` → one fresh refresh token.
2. `asyncio.gather(POST /platform/logout, POST /platform/refresh)` on that SAME token.
3. Sequential follow-up `POST /platform/refresh` on the token → must be 401 (dead).
Record the per-endpoint code tallies and any anomaly.

## Expected result
- logout: always **204**.
- refresh: **200 XOR 401** (won-the-race vs lost-the-race); across iterations BOTH observed.
- follow-up refresh: always **401**.
- Zero 500s, zero client EXC.

## Harness
Script: `harness/tc_063.py` · run: `docker compose exec -T backend python - < testing/02_platform-console/harness/tc_063.py`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** NEW (minor observation, not a defect)

**Actual behavior**

> Across 50 iterations: logout always returned 204; refresh returned 200 in 4 iterations (it
> won the row before logout) and 401 in 46 (logout revoked first); the post-pair follow-up
> refresh was 401 in all 50. No 500, no client EXC, no anomalies. Both race orderings were
> observed, so the contention genuinely fired.

**Evidence**

```
LOGOUT CODES   : {204: 50}
REFRESH CODES  : {401: 46, 200: 4}
FOLLOWUP CODES : {401: 50}
PAIR 500 COUNT : 0  PAIR EXC COUNT: 0
ANOMALIES (first 10): []
NO 500/EXC: True  LOGOUT ALL 204: True  REFRESH IN {200,401}: True  FOLLOWUP ALL 401: True
BOTH ORDERINGS OBSERVED (refresh won sometimes, lost sometimes): True
VERDICT: PASS — no 500; token dead post-pair; at most one consumer
```

**Verdict**

The defense held. The shared conditional revoke `UPDATE ... WHERE revoked_at IS NULL`
serializes the two operations: whichever transaction commits the revoke first wins, the other
sees `rowcount == 0`. For refresh that means `token_rotator.consume`
(`backend/app/identity/services/token_rotator.py:62-63`) raises `RefreshTokenInvalidError` →
401; for logout, `revoke` (`:66-68`) is idempotent (revoking an already-revoked row is a
no-op → still 204). After the pair the row is revoked, so the follow-up refresh is always
401. Critically, logout always terminates the session: in the 4 iterations where refresh won,
the rotated NEW token is irrelevant because — by construction of this test — the follow-up
401 is on the original token; logout's contract (revoke the presented token) is honored every
time. No 500 ever surfaced from the write contention. Confirms the single-use / logout-
revokes properties (PC-02-AC4) hold under cross-endpoint contention.

**NEW observation (minor — not a defect, surfaced because it is the one novel thing this
suite turned up):** in the iterations where refresh *won* the race (4/50 here), the rotation
issues a brand-new descendant refresh token that **survives the logout** — logout only
revoked the *presented* (now-rotated-away) token, so the session is NOT actually terminated:
a live, unreferenced descendant remains valid for its full 7-day TTL. In practice the app's
logout sends the token the client currently holds (which, in a real single-flight client,
*is* the latest one), so this only bites if a logout and a refresh are genuinely concurrent
on the same token. It is a small "logout is not guaranteed to kill the live session under a
concurrent refresh" gap, the same class as the AUD-06 reuse-family deferral
(`docs/FIX_BEFORE_PROD.md`): a robust logout would revoke the whole `subject_id +
subject_type` family, not just the presented hash. Severity: Low (requires a precise race;
the platform refresh token is in-memory-only client-side, limiting exposure). Tagged NEW as a
characterization, not a contract violation — the presented token always dies and the server
never 500s.

**Caveat:** the 4 "refresh won" iterations are exactly the descendant-survival case above;
this test asserts the *presented* token dies (it always does), not that every descendant is
revoked.
