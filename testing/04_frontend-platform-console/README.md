# Target 04 — Frontend (Platform Console + identity auth client) — Adversarial Validation

> Dynamic, browser-driven (Playwright) validation of the **frontend** auth + console UI
> against the live SPA at `http://localhost:5173`. Companion to the backend pass
> (`testing/02_platform-console/`) and the static reviews
> `docs/audits/2026-06-01_platform-console-pr1-review.md` (22 findings, incl. **sec-1**) and
> `2026-06-01_platform-session-pr2-review.md`. Case code **`FE`** (`TC-FE-NNN`).
>
> See `testing/README.md` for the strategy, legend, and finding tags.

## Scope

**In scope (this pass):**
- **sec-1 (the headline):** the **platform** refresh token is held **in memory only**, never in
  `localStorage`/`sessionStorage` — so an injected script can't exfiltrate a 7-day Ethera-staff
  credential. Contrast: the **company** refresh token *is* persisted (deliberate SPA trade-off).
- **Access token never in storage** (memory only, both domains).
- **Role-aware routing is a UX gate only:** `PlatformRoute`/`ProtectedRoute`/`RoleHome` — anon→`/login`,
  company user→`/`, platform admin→`/platform`; loading shows a skeleton (no flicker). Server stays
  authoritative via `aud` (a spoofed client role reaches the shell but gets 401s — no data).
- **Hard-refresh posture:** platform session drops to `/login` (in-memory token gone — *deliberate*);
  company session rehydrates from the stored refresh token.
- **Half-open teardown:** a `/platform/me` failure after login tears the session down (best-effort
  `/platform/logout`) and stays on `/login`.
- **Single-flight refresh:** concurrent 401s share one rotation (AUD-11) — single-use rotation would
  otherwise self-revoke.
- **Stored-XSS render:** `org_name` / `admin_full_name` carrying markup render as inert text (React
  escaping; no `dangerouslySetInnerHTML` exists) across the console list, crest, and credential panel.
- **Logout** clears the persisted token and the session.

**Out of scope:** backend `/platform/*` contracts (Target 02); company user-management UI; visual/a11y
regression (covered by the PR-1 review's component tests).

## Environment

- SPA: `http://localhost:5173` (Vite). API: `http://localhost:8000`. Driver: **Playwright (MCP)**.
- Demo accounts (dev-only): platform `super@ethera.ai` / `Sup3r-Dev-Only-2026!`;
  company admin `admin@demo.oneai` / `Adm1n-Dev-Only-2026!`; member `member@demo.oneai` / `Memb3r-Dev-Only-2026!`.
- Evidence is captured as `localStorage`/`sessionStorage` dumps, route/redirect observations, network
  logs, and console/dialog checks — recorded into each `TC-FE-NNN_*.md`.

## Key facts (the levers)

- `frontend/src/identity/authClient.ts`: access token in module memory; **company** refresh →
  `localStorage["oneai.refresh_token"]`; **platform** refresh → `platformRefreshInMemory` (memory only).
  `refreshInFlight` shares one rotation. `authorizedFetch` retries a 401 exactly once.
- `frontend/src/identity/AuthProvider.tsx`: mount bootstrap only if a stored (company) refresh exists;
  `platformLogin` resolves the real identity via `/platform/me` and tears down a half-open session on
  failure. Client role gates UX only.
- No `dangerouslySetInnerHTML`/`innerHTML`/`eval` in `frontend/src` (grep-confirmed) → XSS render is
  expected inert; proven empirically here.

## Status dashboard

> Result: ⬜ not run · ✅ pass (defense held) · ❌ fail (a defect — the win) · ⚠️ pass-with-concern.
> Tag: 🆕 NEW · ✔ CONFIRMS-FIXED · ✖ REFUTES-FIX · 📋 CONFIRMS-DOCUMENTED · — n/a.
> **Run 2026-06-01** (Playwright MCP against the live SPA). **9 cases · 8 ✅ · 1 ❌ (NEW, the win).**
> The console UI auth model is otherwise sound; the one defect is a prod-reachable multi-tab refresh race.

| Case | Title | Type | Result | Tag |
|---|---|---|---|---|
| TC-FE-001 | Platform refresh token never persisted (sec-1) ⭐ | Adversarial | ✅ | ✔ CONFIRMS-FIXED |
| TC-FE-002 | Access token never in storage; company refresh = only persisted token | Adversarial | ✅ | 📋 CONFIRMS-DOCUMENTED |
| TC-FE-003 | Role-aware routing is a UX gate (anon/company/platform) | Negative | ✅ | ✔ CONFIRMS-FIXED |
| TC-FE-004 | Hard-refresh posture (platform→/login; company rehydrates ×4) | Adversarial | ✅¹ | ✔ CONFIRMS-FIXED |
| TC-FE-005 | Stored-XSS render of org_name / full_name is inert ⭐ | Adversarial | ✅ | ✔ CONFIRMS-FIXED |
| TC-FE-006 | Logout clears the persisted token + revokes server-side | Positive | ✅ | ✔ CONFIRMS-FIXED |
| TC-FE-007 | Half-open teardown on /platform/me failure | Adversarial | ✅ | ✔ CONFIRMS-FIXED |
| TC-FE-008 | Client role is UX-only — server stays authoritative | Adversarial | ✅ | ✔ CONFIRMS-FIXED |
| **TC-FE-009** | **Multi-tab / concurrent refresh collision wipes the shared session** | Concurrency | **❌** | **🆕 NEW** |

¹ Passes for a single isolated context; the cross-context defect it first hinted at is owned by TC-FE-009.

### Headline results

- **sec-1 holds (TC-FE-001 ⭐):** platform login persisted **nothing** (empty `localStorage`/`sessionStorage`/
  cookies) while the session ran live with the real identity — non-vacuous via the company contrast
  (TC-FE-002), which *does* persist its opaque refresh token (the tracked httpOnly-cookie deferral).
- **No stored XSS (TC-FE-005 ⭐):** an `<img onerror>` org name rendered as inert escaped text in both the
  onboard success heading and the `CompanyCard`; sentinel never fired, zero injected elements (React
  escaping; no `dangerouslySetInnerHTML` in `frontend/src`).
- **Fail-safes work live:** half-open teardown on `/platform/me` failure (TC-FE-007), single-flight
  rehydration across 4 reloads (TC-FE-004), logout revoke (TC-FE-006), and the UX-only role gate backed by
  the server's audience check (TC-FE-008 / Target 02).

### NEW defect (TC-FE-009) — multi-tab refresh collision

- An isolated `/auth/refresh → 401 → /login` surfaced organically (TC-FE-004). Rather than dismiss it as a
  dev-only fluke, the investigation it prompted found a real defect: the AUD-11 single-flight guard
  (`refreshInFlight`) is **module-scoped (per-tab)**, while the company refresh token is in **shared
  `localStorage`**. Two tabs can both refresh the same token; the loser's 401 → `setTokens(null)` →
  `removeItem` **wipes the winner's valid token**. Reproduced live: winner rotated to a valid `T2`, the loser
  wiped it (`EMPTY`), `T2` confirmed still-live (200), reload → `/login`. **Prod-reachable,
  StrictMode-independent**, but a **narrow** timing window (both tabs must read the same token within the
  ~tens-of-ms refresh round-trip — `performRefresh` re-reads storage, which often saves the loser); severity
  **Medium** (spurious cross-tab logout; not a security breach). Fix: **compare-and-clear** (narrows it) or a
  cross-tab Web Lock (robust); the tracked httpOnly-cookie deferral also dissolves it. Full detail:
  `TC-FE-009`. (Single-flight robustly protects the single-tab case, so the lone organic 401's exact trigger
  was not isolated — not claimed to be StrictMode.)
