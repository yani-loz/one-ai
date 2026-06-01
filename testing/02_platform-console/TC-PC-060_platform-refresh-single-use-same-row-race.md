# TC-PC-060: Platform refresh single-use under same-row contention

| Field | Value |
|---|---|
| **ID** | TC-PC-060 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | RACE — Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove that a single platform refresh token can be consumed **exactly once** even when many
clients present it simultaneously — the single-use rotation guarantee (PC-02-AC4, audit
AUD-01) under same-row contention.

## Break hypothesis
If the revoke is not atomic (e.g. a read-then-write TOCTOU instead of a conditional
`UPDATE ... WHERE revoked_at IS NULL`), two or more concurrent refreshers both read the
token as live and both mint a new pair → **>1 → 200**, breaking single-use and handing an
attacker a way to fork a session from a stolen-then-reused token. A 500 under contention
(deadlock / unhandled IntegrityError) would also be a defect.

## Preconditions
- Live stack up (`docker compose up`); demo platform admin `super@ethera.ai` (never mutated —
  only used to mint one token).
- Run-stamp: none persisted to DB (refresh_tokens has no slug/email column); the invariant
  is proven entirely at the HTTP layer via the response tally.

## Steps
1. `POST /platform/login` once → capture ONE refresh token (the contested row).
2. Fire **60** concurrent `POST /platform/refresh`, all presenting that SAME token.
3. `summarize()` the status tally; print one sample 200 body and one 401 body.
4. Rotate the single winner's NEW refresh token once → must be 200 (a real new pair issued).

## Expected result
Exactly **1 → 200** and **59 → 401** (`{'detail':'Refresh token is invalid.'}`); zero 500s;
zero client EXC. Winner's new token rotates (200).

## Harness
Script: `harness/tc_060.py` · run: `docker compose exec -T backend python - < testing/02_platform-console/harness/tc_060.py`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> 60 concurrent presentations of one refresh token resolved to exactly one success and 59
> rejections. No 500, no client-side timeout. The winner's freshly-issued token itself
> rotated, proving a genuine new pair was minted (not a no-op 200).

**Evidence**

```
MINTED one platform refresh token; firing 60 concurrent /platform/refresh
TALLY: {200: 1, 401: 59}
SAMPLE 200 BODY keys: ['access_token', 'refresh_token', 'token_type']
SAMPLE 401 BODY: {'detail': 'Refresh token is invalid.'}
COUNTS  200: 1  401: 59  500: 0  EXC: {}
FOLLOW-UP rotate of winner's new token -> 200
VERDICT: PASS — exactly 1x200 + 59x401 (corroborates single-use, same-row caveat)
```

**Verdict**

The defense held: single-use rotation is atomic under 60-way same-row contention. The
mechanism is the conditional revoke at `backend/app/identity/repositories/refresh_token_repository.py:50-55`
(`UPDATE ... WHERE token_hash=? AND revoked_at IS NULL`, returning `rowcount`), gated in
`backend/app/identity/services/token_rotator.py:62-63` (`if revoke_by_hash(...) == 0: raise
RefreshTokenInvalidError`). Confirms AUD-01 / PC-02-AC4.

**CAVEAT (as mandated):** this is a SAME-ROW race — all 60 requests contend on one
`refresh_tokens` row and serialize on that row's write lock *by design*. A clean result
therefore **corroborates** the single-use guarantee (independently confirmed by code review
of `token_rotator.consume`) but is **not independent proof** the way the different-row
onboard races (TC-PC-061/062) are, where a UNIQUE-violation artifact proves contention truly
engaged. Tagged CONFIRMS-FIXED accordingly.

**Notes / follow-up**

Independent contention proof lives in TC-PC-061/062 (different-row). Logout-vs-refresh
contention on the same row is TC-PC-063.
