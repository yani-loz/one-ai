# TC-OL-061: Refresh-after-suspend — 50 pre-suspension tokens all blocked, none slip through

| Field | Value |
|---|---|
| **ID** | TC-OL-061 · **Target** Org Lifecycle (PC-03a) · **Suite** RACE |
| **Type** | Concurrency · **Severity if fail** High · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-FIXED (PC-03a-AC3 under load) |

## Objective
Under suspension, no pre-suspension refresh token can mint a new session — even with many tokens fired
concurrently. The refresh gate has no leak path.

## Steps / Harness
`provision_company("race061")` → 50 concurrent logins of the same admin → 50 distinct refresh tokens →
suspend → fire all 50 refreshes concurrently. `harness/_finish_race.py` (061).

## Execution result
- **Run at:** 2026-06-01 local · **Result:** ✅ Pass · **Tag:** CONFIRMS-FIXED

**Evidence**
```
[061] 50 pre-susp tokens refreshed under suspension: {403: 50} (expect all 403, none slip a token)
```

**Verdict**
Defense held under load. All 50 concurrent refreshes of distinct pre-suspension tokens → 403, zero 200, zero
5xx/EXC. `AuthService.refresh` gates every path on `_load_loginable_org` (`auth_service.py:103`) after
consuming the token, so a suspended org cannot extend any session regardless of how many tokens are presented
concurrently. No leak. (Per TC-OL-003, those tokens also survive — they rotate again after reactivation.)
