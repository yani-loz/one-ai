# TC-FE-001: Platform refresh token is never persisted (sec-1) ⭐

| Field | Value |
|---|---|
| **ID** | TC-FE-001 |
| **Target** | Frontend (Platform Console + auth client) |
| **Suite** | Token storage / sec-1 |
| **Type** | Adversarial |
| **Severity if it fails** | High (XSS exfil of a 7-day Ethera-staff credential) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
The high-privilege **platform** refresh token must live in memory only — never in
`localStorage`/`sessionStorage`/cookies — so an injected script cannot exfiltrate it (PR-1 sec-1).

## Break hypothesis
After a platform login, a token-shaped value (the 7-day refresh token) appears in
`localStorage`/`sessionStorage`, readable by any script → XSS = platform takeover.

## Preconditions
SPA at :5173, storage cleared. Platform admin `super@ethera.ai`.

## Steps
1. Clear storage, load `/login`.
2. Platform login (dev quick-fill → Sign in) → lands `/platform`.
3. Dump `localStorage`, `sessionStorage`, `document.cookie`; scan every value for JWT/opaque-token shapes.
4. Confirm the session is actually live (console rendered with real identity) → the proof is non-vacuous.

## Expected result
No `oneai.refresh_token` and no token-shaped value in any web storage or cookie; session still works.

## Harness
Playwright MCP (`browser_evaluate` storage dump). Run interactively against :5173.

---

## Execution result

- **Run at:** 2026-06-01 ~10:59 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**
> Authenticated on `/platform` (header "super@ethera.ai · Platform admin" — the real identity from
> `/platform/me`; 23 company cards rendered), yet **all** client storage was empty.

**Evidence**
```
url: /platform
localStorage_full: {}        sessionStorage_full: {}        cookies: ""
refresh_key_present: false   any_token_shaped_values: []
/platform header: "super@ethera.ai · Platform admin"  (23 companies, sealed banner) → session live
```

**Verdict**
Defense held. The platform refresh token is held in module memory only
(`authClient.ts` `platformRefreshInMemory`; `setTokens(tokens, false)` in `platformLogin`), never
written to storage. **Non-vacuous:** the session is fully functional (real identity + fleet loaded), so
a working credential exists — it simply isn't persisted. sec-1 holds. Discriminating contrast: TC-FE-002
shows the *company* login *does* persist its refresh token, so the empty platform storage is a deliberate
asymmetry, not an artifact of "nothing logged in."

**Notes / follow-up**
The deliberate consequence (no hard-refresh survival) is verified in TC-FE-004.
