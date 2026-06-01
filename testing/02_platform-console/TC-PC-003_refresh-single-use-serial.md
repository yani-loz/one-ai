# TC-PC-003: Refresh single-use (serial) — old token rejected after one rotation

| Field | Value |
|---|---|
| **ID** | TC-PC-003 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PSES — Session lifecycle |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-02-AC4 (single-use, serial): a platform refresh token may rotate **once**; presenting
the SAME (now-rotated) token again is rejected with 401.

## Break hypothesis
A violation = the old refresh token rotates a **second** time (replay → new pair), meaning
rotation is not single-use and a captured refresh token is reusable.

## Preconditions
- Live stack; demo platform admin seeded. PSES suite; no orgs created.
- **Scope note:** this is the SERIAL proof. Per the target README, a black-box serial pass is
  "corroboration confirmed by code review, not independent proof"; the concurrent same-row
  race is the RACE suite's case. This case does NOT add concurrency and does not overclaim it.

## Steps
1. `platform_login_pair()` → (_, old_refresh).
2. `POST /platform/refresh` with old_refresh → expect 200.
3. `POST /platform/refresh` with the SAME old_refresh again → expect 401.

## Expected result
First rotation `200`; reuse of the rotated token `401` `{"detail":"Refresh token is invalid."}`.

## Harness
Script: `harness/tc_003.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_003.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> First rotation succeeded (200, new pair issued). Re-presenting the now-rotated token returned
> 401 with the generic "Refresh token is invalid." — single-use held.

**Evidence**

```
FIRST-ROTATION STATUS: 200 BODY: {'access_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...', 'refresh_token': 'N-c0ARFbKpqvvFD7wFb1o9mvDVL9yDDIasFdx6Bd7OJLfBx6MAYYFU1PuiKDD79j', 'token_type': 'bearer'}
REUSE-OLD STATUS: 401 BODY: {'detail': 'Refresh token is invalid.'}
SINGLE-USE-HELD: True
```

**Verdict**

Defense held. `TokenRotator.consume` (`token_rotator.py:34-64`) revokes via a conditional
`UPDATE ... WHERE revoked_at IS NULL`; the second presentation touches zero rows → generic
`RefreshTokenInvalidError` → 401. Serial single-use confirmed; the concurrency proof belongs to
the RACE suite (same-row lock serialization by design).

**Notes / follow-up**

The 401 reuses the same generic message as unknown/expired tokens — no oracle distinguishing
"revoked" from "unknown" (constant-shape failure, token_rotator.py:56-63).
