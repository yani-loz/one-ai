# TC-FE-004: Hard-refresh posture — platform drops to /login; company rehydrates

| Field | Value |
|---|---|
| **ID** | TC-FE-004 |
| **Target** | Frontend (session bootstrap) |
| **Suite** | Session lifecycle |
| **Type** | Adversarial |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass (single-context posture) — the anomaly below is a confirmed defect, see **TC-FE-009** |
| **Finding tag** | CONFIRMS-FIXED (single-context) |

## Objective
By design: a **platform** session does NOT survive a hard refresh (in-memory token gone → `/login`); a
**company** session DOES (rehydrates via `/auth/refresh` from the stored token, single-flight, AUD-11).

## Break hypothesis
(a) A platform session survives a reload (would mean the token was persisted — sec-1 break); or
(b) a company session fails to rehydrate and is wrongly bounced to `/login`.

## Steps
1. Platform admin on `/platform` → full page load `/` → expect `/login`.
2. Company admin on `/` → reload ×4 → expect to stay authenticated each time; observe the refresh token rotating.

## Expected result
Platform → `/login`; company → stays on `/` authenticated; one `/auth/refresh` per reload (single-flight).

## Harness
Playwright MCP navigation + `browser_network_requests` + `browser_evaluate`.

---

## Execution result

- **Run at:** 2026-06-01 ~11:00–11:05 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior / Evidence**
```
PLATFORM: login → /platform; goto('/') (hard load) → /login   (getStoredRefreshToken()===null → no /auth call)
COMPANY (clean single reload) network: GET /auth/me 401, GET /auth/me 401 (StrictMode 2x),
         POST /auth/refresh 200 (single-flight deduped), GET /auth/me 200, GET /auth/me 200
COMPANY reloads ×4: url stayed "/", authed=true each time; refresh token rotated every reload
         CbMq… → Gncw… → L9kN… → UT8f…
```

**Verdict**
Defense held both ways. Platform: nothing persisted ⇒ bootstrap short-circuits to unauthenticated ⇒
`/login` (the intended posture, not a bug). Company: `AuthProvider` bootstrap sees the stored refresh
token, `/auth/me` 401 → single-flight `refreshTokens()` rotates once (despite StrictMode double-mount) →
rehydrated; 4/4 reloads survived and rotated the token (single-use working).

**Notes / follow-up**
The single-context posture is correct: platform→`/login`, company rehydrates (4/4 reloads, token rotating;
the single-flight guard robustly protects one tab — `refreshInFlight` is set synchronously before the inner
fetch yields). The one organic `/auth/refresh → 401 → /login` seen during rapid navigation is **consistent
with** the concurrent-refresh hazard class — but its **exact trigger was not isolated** here, so it is not
claimed to be any specific cause. What that anomaly prompted *was* isolated: the real, prod-reachable
**multi-tab refresh-collision defect**, reproduced and root-caused in **TC-FE-009** (the AUD-11 single-flight
guard is per-tab; a losing concurrent refresh wipes the shared `localStorage` token). This case passes for a
single isolated context; the cross-context defect is owned by TC-FE-009.
