# TC-FE-007: Half-open teardown when /platform/me fails after login

| Field | Value |
|---|---|
| **ID** | TC-FE-007 |
| **Target** | Frontend (AuthProvider.platformLogin) |
| **Suite** | Session lifecycle / fail-safe |
| **Type** | Adversarial |
| **Severity if it fails** | Medium (orphaned high-privilege in-memory token) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
If `/platform/login` succeeds (in-memory platform token set) but the follow-up `GET /platform/me`
fails, the session must be torn down (best-effort `/platform/logout` revoke + cleared) and the user kept
on `/login` — no orphaned high-privilege token lingers (PR-2 corr-1 / test-2).

## Break hypothesis
A `/platform/me` failure leaves the in-memory platform token set with `status` never flipping — a
half-open session: not visibly logged in, but a live 7-day refresh token sits in memory.

## Preconditions
Platform admin. `window.fetch` patched so `/platform/me` → 500 (login + logout pass through); counters on
`/platform/me` and `/platform/logout`.

## Steps
1. Install the fetch override on `/login`.
2. Drive the platform login (dev quick-fill → Sign in).
3. Assert: stays on `/login`, a connectivity error is shown, `/platform/me` was hit, and `/platform/logout`
   was called (teardown). Restore fetch.

## Expected result
URL `/login`; error message; `me_calls ≥ 1`; `logout_calls ≥ 1`; no residual storage.

## Harness
Playwright MCP `browser_evaluate` (fetch monkeypatch + counters) + clicks.

---

## Execution result

- **Run at:** 2026-06-01 ~11:10 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior / Evidence**
```
after platform Sign in (with /platform/me forced 500):
  url=/login (still_on_login=true)
  me_calls=1   logout_calls_teardown=1
  error shown: "Couldn't reach the server. Check that the API is running, then try again."
  residual storage after restore: local=[] session=[]
```

**Verdict**
Defense held. `AuthProvider.platformLogin` wraps `fetchCurrentPlatformAdmin()` in `.catch(async () => {
await logoutRequest(); throw error; })` — so the failed `/platform/me` triggered a best-effort
`/platform/logout` (counted), `setTokens(null)` cleared the in-memory platform token, the error
re-surfaced to `LoginPage`, and the UI stayed on `/login` with a connectivity message. No orphaned
high-privilege token remained.

**Notes / follow-up**
Mirrors PR-2 unit test `LoginPage.test.tsx::…me_fails_clears_session_and_stays_on_login`; proven here live.
