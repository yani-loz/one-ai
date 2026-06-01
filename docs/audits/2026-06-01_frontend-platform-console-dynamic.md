# Frontend (Platform Console + identity auth client) — Dynamic Adversarial Validation

> **Scope:** the PC-01 console UI + the shared identity auth client (`frontend/src/platform/*`,
> `frontend/src/identity/{authClient,AuthProvider,ProtectedRoute}.tsx`, `platform/PlatformRoute.tsx`)
> driven through the **live SPA** at `http://localhost:5173` with **Playwright (MCP)**. Companion to the
> backend pass (`docs/audits/2026-06-01_platform-console-dynamic-adversarial.md`) and the PR-1/PR-2 static
> reviews. Suite + per-case evidence: `testing/04_frontend-platform-console/`.
>
> **Method:** 8 browser-driven cases (`TC-FE-001..008`), each author→execute→record, evidence captured as
> storage dumps, redirect/URL observations, network logs, and an XSS execution sentinel.

## Executive summary

**9 cases · 8 ✅ · 1 ❌ (1 NEW, Medium).** The console's frontend auth model is **sound** on every
security-critical claim; the one defect is a prod-reachable **multi-tab refresh-collision** correctness
bug (spurious cross-tab logout — not a security breach), found by chasing an anomaly instead of dismissing
it. The two highest-stakes security claims hold live and **non-vacuously**:

1. **sec-1 — the platform refresh token is never persisted (TC-FE-001 ⭐).** After a platform login the
   session ran live (real identity from `/platform/me`, 23 company cards) with **empty**
   `localStorage`/`sessionStorage`/cookies. The discriminating contrast (TC-FE-002): a *company* login
   *does* persist its opaque refresh token, so the empty platform storage is a deliberate asymmetry, not
   "nothing logged in." An injected script cannot read the 7-day Ethera-staff credential from storage.
2. **No stored XSS in the super-admin console (TC-FE-005 ⭐).** An `<img src=x onerror=…>` org name
   (allowed by the backend `SafeName`, so it reaches the client) rendered as **inert escaped text** in
   both the onboard success heading and the `CompanyCard` list — sentinel never fired, zero injected
   elements. Root: React's default text interpolation + **no `dangerouslySetInnerHTML`/`innerHTML`/`eval`
   anywhere in `frontend/src`** (grep-confirmed).

Supporting fail-safes all verified live: UX-only role routing backed by the server audience gate
(TC-FE-003/008), the deliberate platform hard-refresh→`/login` vs. company single-flight rehydration
across 4 reloads (TC-FE-004), logout revoke + storage clear (TC-FE-006), and the half-open-session
teardown when `/platform/me` fails after login (TC-FE-007 — `/platform/logout` fired, stayed on `/login`,
no orphaned in-memory token).

## Documented exposure re-confirmed (not new)

- **Company refresh token in `localStorage` (TC-FE-002, CONFIRMS-DOCUMENTED).** The company session
  persists its opaque refresh token (XSS-readable) — the deliberate SPA trade-off already tracked in
  `FIX_BEFORE_PROD.md` (*Move the refresh token to an httpOnly cookie*). The **platform** side already
  applies the in-memory hardening (sec-1); closing the cookie item removes the company-side exposure too.

## NEW finding — F-FE-01: multi-tab / concurrent refresh collision (Medium)

*Case TC-FE-009. Reproduced live; root cause established by code + evidence.*

An isolated `/auth/refresh → 401 → /login` first surfaced organically during navigation (TC-FE-004).
Rather than dismiss it as a StrictMode dev fluke, it was chased to root cause — a **real, prod-reachable
correctness defect**:

- The single-flight refresh guard (AUD-11) is a **module-level `refreshInFlight`** — it dedupes concurrent
  refreshes **within one tab only**. The company refresh token lives in **shared `localStorage`**.
- Two contexts (tabs) can both read token `T1` and both `POST /auth/refresh(T1)`. One wins (rotates `T1→T2`,
  `setTokens(pair)` stores `T2`); the loser gets 401 → `setTokens(null)` → `localStorage.removeItem(...)`
  (`authClient.ts:64-68`), **wiping the winner's freshly-stored valid token**. Both tabs end logged out.

**Live evidence (TC-FE-009):** winner rotated to `T2` (`74RdK-sx…`, persisted); loser's 401 wiped storage to
`EMPTY`; `T2` confirmed **still a live token** (re-rotated, 200) — so a valid session was destroyed; a
reload then landed on `/login`. **StrictMode-independent** (two tabs always have two module contexts).

**Timing window / severity (honest triage):** real `performRefresh` re-reads `getStoredRefreshToken()` at
call time, so the loser only collides if **both tabs read the same token within the refresh round-trip
(~tens of ms)** before either writes back; outside that window the second tab reads the rotated token and
succeeds. The repro faithfully forces that both-captured-`T1` sub-case but bypasses the saving re-read — so
the window is genuine but **narrow**, putting this at the top of a **Low–Medium** band (labelled Medium:
multi-tab is normal for a console operator). It is a **UX/correctness** defect (unexpected logout of a valid
session), **not** a security breach (no token leak, no auth bypass). Note: single-flight *does* robustly
protect the single-**tab** case (`refreshInFlight` is set synchronously before the fetch yields), so the lone
organic dev `/auth/refresh→401` is consistent with this hazard class but its exact trigger was not isolated
— **not** claimed to be a StrictMode double-mount.

**Remediation:** *compare-and-clear* (cheap, narrows it) — in `performRefresh`'s failure branch, only
`setTokens(null)` if the **currently stored** token still equals the one whose refresh failed (else another
tab rotated it; adopt the new value and retry). This is check-then-act and not atomic, so it shrinks but
does not fully close the window. The **robust** fix is a cross-tab Web Lock (`navigator.locks`) or
`BroadcastChannel`/`storage`-event leader so only one tab rotates. The tracked httpOnly-cookie deferral also
dissolves it (the browser owns the single token). Re-test with two real tabs after the fix.

## Coverage & limitations

- **Driver:** Playwright MCP against the **Vite dev build** (StrictMode on) — which is *why* the bootstrap
  double-mount was observable; a prod-build pass is the natural complement (above).
- **Not covered:** visual/a11y regression (PR-1 component tests own that); CSP `connect-src` enforcement
  (no fetch on load to exercise it — tracked separately in `FIX_BEFORE_PROD.md`); the company
  user-management UI (no such screens yet).
- **Cross-layer cases** (TC-FE-008) lean on the live backend evidence from Target 02 rather than forging
  client React state, which would add nothing over the authoritative server 401s.

## Side effects (dev DB)

- TC-FE-005 created one persistent org `xss-render-test` (markup name) + its admin via the real onboard
  flow. Left in place per the "leave DB as-is" decision; clears on the next `TRUNCATE` + re-seed.
