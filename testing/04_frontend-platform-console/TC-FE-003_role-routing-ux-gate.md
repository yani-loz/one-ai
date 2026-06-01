# TC-FE-003: Role-aware routing is a UX gate (anon / company / platform)

| Field | Value |
|---|---|
| **ID** | TC-FE-003 |
| **Target** | Frontend (routing guards) |
| **Suite** | Routing / authorization UX |
| **Type** | Negative |
| **Severity if it fails** | Medium (UX leak; real authz is server-side) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`PlatformRoute`/`ProtectedRoute`/`RoleHome` route each principal to the right place: anon→`/login`,
company user→`/` (home), platform admin→`/platform`. The role check is a **UX gate only** — the server
stays authoritative via `aud` (TC-FE-008).

## Break hypothesis
A non-platform (or anon) visitor reaches `/platform` and sees the console shell/data.

## Steps
1. **Anon** → navigate `/platform` → expect redirect `/login`.
2. **Company admin** (authenticated) → navigate `/platform` → expect redirect `/` (own home), not the console.
3. **Platform admin** → login → expect `/platform`.

## Expected result
Each redirect resolves to the principal's home; no non-platform principal renders the console.

## Harness
Playwright MCP navigation + `browser_evaluate` URL/body checks.

---

## Execution result

- **Run at:** 2026-06-01 ~11:00–11:05 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior / Evidence**
```
(a) anon → goto /platform        → final URL /login
(b) platform admin login         → final URL /platform (console rendered)
(c) company admin → goto /platform → rehydrated, then final URL / , body "Welcome, Demo Company Admin"
```

**Verdict**
Defense held. `PlatformRoute` (platform/PlatformRoute.tsx): `unauthenticated → <Navigate to="/login">`,
`role !== "platform_admin" → <Navigate to="/">`, platform admin → children; `loading → skeleton` (no
flicker). Case (c) is the meaningful one — an authenticated **company** user is bounced to `/` (home),
never the console. The guard is client-side UX only; server enforcement is proven separately
(TC-FE-008 / Target 02 TC-PC-023).

**Notes / follow-up**
The client-side `RoleHome` (platform admin visiting `/` → `/platform`) is covered by the PR-1 unit test
`App.test.tsx`; a hard `goto('/')` here drops the in-memory platform session (TC-FE-004), so it is not a
client-route transition.
