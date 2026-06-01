# TC-FE-006: Logout clears the persisted token + revokes server-side

| Field | Value |
|---|---|
| **ID** | TC-FE-006 |
| **Target** | Frontend (auth client) |
| **Suite** | Session lifecycle |
| **Type** | Positive |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Logout clears local session state (removes the stored refresh token) and revokes it server-side, then
returns to `/login`.

## Break hypothesis
After logout the refresh token lingers in `localStorage` (re-login on reload) or the server token is not revoked.

## Steps
1. Company admin logged in on `/`. Click **Log out**.
2. Assert final URL `/login`, `localStorage` empty, and a `POST /auth/logout` fired.

## Expected result
`/login`; `oneai.refresh_token` removed; `/auth/logout → 204`.

## Harness
Playwright MCP click + `browser_evaluate` storage + `browser_network_requests`.

---

## Execution result

- **Run at:** 2026-06-01 ~11:06 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior / Evidence**
```
after Log out: url=/login; localStorage_keys=[]; sessionStorage_keys=[]; refresh_token_present=false
network: POST /auth/logout → 204 (idempotent revoke)
```

**Verdict**
Defense held. `logout()` (authClient.ts) calls `POST /auth/logout` (domain-aware) then `setTokens(null)`,
clearing `localStorage`/in-memory state even if the network call fails (best-effort), and `AuthProvider`
flips to `unauthenticated` → guard redirects to `/login`. Server-side revoke confirmed (204).

**Notes / follow-up**
Backend logout revocation + idempotency proven independently in Target 02 (TC-PC-004/005).
