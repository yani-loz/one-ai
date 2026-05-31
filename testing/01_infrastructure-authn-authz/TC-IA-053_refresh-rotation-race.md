# TC-IA-053: Refresh-rotation race — N concurrent /auth/refresh yields exactly one new pair

| Field | Value |
|---|---|
| **ID** | TC-IA-053 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the AUD-01 fix: refresh-token rotation is **single-use under contention**. N concurrent
`/auth/refresh` presenting the *same* raw token must mint exactly one new pair; all others must be
rejected.

## Break hypothesis
If the revoke were a blind `UPDATE ... WHERE id=...` (the pre-AUD-01 bug), two concurrent refreshes would
both read `revoked_at IS NULL`, both pass the Python check, both `issue_pair`, both commit → **two valid
pairs from one token**, breaking single-use. The fix is a conditional `UPDATE ... WHERE token_hash=:h AND
revoked_at IS NULL` (`refresh_token_repository.revoke_by_hash:44`) whose `rowcount==0` →
`RefreshTokenInvalidError` (`token_rotator.consume:62`). >1 success in any trial → REFUTES-FIX (High).

## Preconditions
Live stack, persistent DB. Namespace `race-<stamp>-53`. One fresh run-stamped org/admin. 50 trials; each
trial logs in fresh (one raw refresh token) then fires **N=10 concurrent** `/auth/refresh` with that same
token via `asyncio.gather`.

## Steps
1. Onboard org + admin; for each of 50 trials: company login → one raw refresh token.
2. Fire 10 concurrent `POST /auth/refresh {refresh_token: <same raw>}`.
3. Count `200`s per trial (must be exactly 1) and `401`s (must be 9).

## Expected result
Every trial: exactly **1×200 + 9×401**. Aggregate over 50 trials: 50×200, 450×401. Zero trials with >1
success.

## Harness
Script: `harness/tc_053.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_053.py`

---

## Execution result

- **Run at:** 2026-05-31 10:52 local
- **Result:** ✅ Pass (defense held)
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> All 50 trials produced exactly 1×200 + 9×401. Aggregate: 50 successes, 450 failures. Zero trials minted
> more than one valid pair. The single-use rotation invariant held under 10-way contention every time.

**Evidence**

```
TC-IA-053  run=19e7d3740710c78  trials=50  concurrency N=10
setup failures              : 0
aggregate code distribution : {'200': 50, '401': 450}
per-trial success counts    : [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                               1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
trials with success != 1    : 0
VERDICT: CONFIRMS_FIXED — exactly 1x200 + 9x401 in all 50 trials (AUD-01 holds under contention)
```

**Verdict**

Defense **held** — AUD-01 is confirmed fixed. The atomic conditional revoke
(`UPDATE ... WHERE token_hash=:h AND revoked_at IS NULL`, `refresh_token_repository.py:50-54`) means only
one of N presentations can flip `revoked_at`; the rest get `rowcount==0` → `RefreshTokenInvalidError`
(`token_rotator.consume:62`).

**Strength of evidence — corroborated, not proven by this result alone.** 1×200 + 9×401 is *also* exactly
the serial outcome, and this is a **same-row** contention pattern: even genuinely-concurrent requests
serialize on the row lock the conditional `UPDATE` takes, so a black-box pass cannot by itself distinguish
"raced and the lock held" from "ran in series." Unlike TC-IA-054/055 — whose db-log `UNIQUE`-constraint
violations are positive artifacts proving the loser reached the INSERT under real interleaving — this case
has **no analogous artifact**, and the TC-IA-050/051/052 firing does **not** transfer here (those are
*different-row* races with no lock to serialize them). The verdict therefore rests on **code review of the
conditional revoke** plus a result fully consistent with single-use; read it as strong corroboration of
AUD-01, not an independent dynamic proof of the concurrency property.

**Notes / follow-up**

No follow-up. This is the fix pattern (conditional `UPDATE ... WHERE`) that TC-IA-050/051/052 recommend
porting to the last-admin guard. AUD-06 (reuse-family revocation) remains a separate, documented
deferral and is not in scope here.
