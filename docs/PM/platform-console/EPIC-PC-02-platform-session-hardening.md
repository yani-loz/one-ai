# EPIC PC-02 — Platform Session Hardening (`/me` + refresh + logout)

| Field | Value |
|---|---|
| **Epic ID** | PC-02 |
| **Module** | Platform Console (`PC`) |
| **Status** | ✅ Done (pending commit) |
| **Branch** | `feat/platform-session` (stacked on `feat/platform-console`) |
| **PR** | PR-2 (backend + frontend) |
| **Depends on** | PC-01 (the console it keeps alive); Identity token plumbing (`TokenIssuer`, `TokenRotator`, `aud`/`subject_type`) |
| **Closes** | **AUD-14** (`docs/FIX_BEFORE_PROD.md`) — synthesised platform identity + no rehydrate endpoint |
| **Review** | [docs/audits/2026-06-01_platform-session-pr2-review.md](../../audits/2026-06-01_platform-session-pr2-review.md) — 4 findings, all fixed |
| **Date** | 2026-06-01 |

## 1. Goal & context

After PC-01 the platform session was a stop-gap: the admin identity was **synthesised
client-side from the typed email**, and the session died after one ~15-min access-token
lifetime (no platform refresh). PC-02 makes the session real and durable **without
weakening the sec-1 hardening** (the platform refresh token must never be persisted):

- The admin's **real identity** comes from `GET /platform/me`.
- The session **refreshes in-tab** via `POST /platform/refresh` (in-memory refresh token).
- `POST /platform/logout` revokes server-side.
- A **hard refresh still re-logs-in by design** — no high-privilege credential is persisted.

## 2. Scope

**In scope**
- Backend: `GET /platform/me`, `POST /platform/refresh`, `POST /platform/logout`
  (mirroring `/auth/*`), `PlatformAdminResponse`, `build_admin_view_by_id`.
- Frontend: platform refresh token held **in memory only**; **domain-aware**
  `performRefresh`/`logout`; `fetchCurrentPlatformAdmin`; `AuthProvider` resolves the real
  identity on login and tears down a half-open session on `/platform/me` failure.

**Out of scope (deliberate / later)**
- Surviving a **hard refresh** — intentionally not done (in-memory tokens; see §7).
- Persisted platform sessions / refresh-token rotation families → not planned (security choice).

## 3. User stories

| ID | Story |
|---|---|
| PC-02-S1 | As a platform admin, my console session **stays alive while the tab is open** (no surprise 15-min logout). |
| PC-02-S2 | As a platform admin, the console shows my **real identity** (name/email), not a guess from my email. |
| PC-02-S3 | As the system, the platform and company auth domains are **strictly confined** (no token crosses over). |
| PC-02-S4 | As the system, refresh is **single-use** and logout **revokes** the token. |
| PC-02-S5 | As the system, the platform refresh token is **never persisted** (in-memory only). |
| PC-02-S6 | As a platform admin, if identity resolution fails after login, I'm not left in a **half-open** session. |

## 4. Acceptance criteria → tests (traceability matrix)

> ⭐ = security-critical (cross-domain confinement is **non-negotiable** per `.claude/rules/testing.md`).
> The PC-02 review (test-1) specifically hardened ACs 3a/3b so the tests **discriminate** the
> guard — they fail if the audience/subject_type check is removed.

| AC | Criterion | Proven by (automated) |
|---|---|---|
| PC-02-AC1 | A 401 during a platform request **rotates via `/platform/refresh`** (the in-memory token), not `/auth/refresh`. | FE `authClient.test.ts::test_platform_session_refreshes_via_platform_endpoint`; BE `test_platform_routes.py::test_platform_refresh_rotates_and_returns_new_pair` |
| PC-02-AC2 | `GET /platform/me` returns the admin's **own** identity (id/email/full_name); `AuthProvider` uses it. | BE `::test_platform_me_returns_admin_identity`; FE `LoginPage.test.tsx::test_platform_toggle_switches_to_platform_login_endpoint_and_lands_on_console` |
| ⭐ PC-02-AC3a | A **company token** is rejected on `GET /platform/me` (audience guard is the *only* thing preventing 200). | BE `::test_platform_me_with_company_token_is_rejected` (token carries a real platform-admin id → discriminating) |
| ⭐ PC-02-AC3b | A **company refresh token** is rejected on `POST /platform/refresh` **without revoking it** (subject_type guard). | BE `::test_platform_refresh_rejects_company_refresh_token` (asserts the company token still rotates at `/auth/refresh` afterwards) |
| PC-02-AC4 | Refresh is **single-use** (reuse → 401); **logout revokes** (post-logout refresh → 401). | BE `::test_platform_refresh_reuse_of_rotated_token_returns_401`, `::test_platform_logout_revokes_refresh_token` |
| ⭐ PC-02-AC5 | The platform refresh token is **never written to localStorage**; logout hits `/platform/logout`. | FE `authClient.test.ts::test_platform_login_does_not_persist_refresh_token`, `::test_platform_logout_revokes_via_platform_endpoint` |
| PC-02-AC6 | A `/platform/me` failure after login **tears down** the half-open session (logout + cleared) and shows a connectivity error, staying on login. | FE `LoginPage.test.tsx::test_platform_login_succeeds_but_me_fails_clears_session_and_stays_on_login` |
| PC-02-AC7 | An unknown/deactivated admin id in a valid platform token → **401** (token outlived account). | BE `::test_platform_me_unknown_admin_returns_401` |
| PC-02-AC8 | Missing token on `GET /platform/me` → **401** (not 403). | BE `::test_platform_me_without_token_is_rejected` |

## 5. Implementation map (requirement → code)

| Area | Files |
|---|---|
| Endpoints | `backend/app/identity/routes/platform_routes.py` (`/me`, `/refresh`, `/logout`) |
| Service + schema | `services/platform_auth_service.py` (`build_admin_view_by_id`), `schemas/platform_schemas.py` (`PlatformAdminResponse`) |
| Token mechanics (reused) | `security/tokens.py` (audience), `services/token_rotator.py` (subject_type single-use), `services/token_issuer.py`, `dependencies.py` (`get_current_platform_admin`) |
| FE token client | `frontend/src/identity/authClient.ts` (in-memory platform refresh; domain-aware `performRefresh`/`logout`; `fetchCurrentPlatformAdmin`) |
| FE session | `frontend/src/identity/AuthProvider.tsx` (real identity + half-open teardown), `types.ts` (`PlatformAdminView`) |

## 6. Manual / QA test plan

> Pre-req: stack up + seeded. ⚠️ Running the **backend test suite wipes the seed** — re-seed
> after (`docker compose exec backend uv run python -m scripts.seed_identity`).

1. **Real identity** — sign in as Platform admin → console header shows the real name
   ("Ethera Super Admin" via `/platform/me`), not a guess from the email local-part.
2. **In-tab survival** — stay signed in past the access-token lifetime (or shorten it in
   config) and keep clicking around → you are **not** kicked to login; the network tab
   shows a `POST /platform/refresh` rotating the token.
3. **Hard refresh (deliberate)** — press F5 → you land on `/login` (in-memory token gone).
   This is expected, not a bug.
4. **Cross-domain (security)** — with browser devtools, take a **company** access token and
   call `GET /platform/me` → **401**. Take a **company refresh** token, call
   `POST /platform/refresh` → **401**, then confirm that company refresh **still works** at
   `POST /auth/refresh` (it wasn't revoked).
5. **Logout** — log out → network shows `POST /platform/logout`; the refresh token no longer
   rotates.

   *(API-level smoke, no browser — values via `curl`):*
   ```bash
   curl -s -X POST :8000/platform/login -d '{"email":"super@ethera.ai","password":"Sup3r-Dev-Only-2026!"}' -H 'Content-Type: application/json'
   curl -s :8000/platform/me -H "Authorization: Bearer <ACCESS>"            # 200 real identity
   curl -s -X POST :8000/platform/refresh -d '{"refresh_token":"<REFRESH>"}' -H 'Content-Type: application/json'  # 200 rotates
   curl -s :8000/platform/me -H "Authorization: Bearer <COMPANY_ACCESS>"     # 401 (audience)
   ```
   (All three were **live-verified** during the build — see the audit's "Notes carried forward".)

## 7. Non-functional / security

- **Audience + subject_type confinement** is the core security property (AC3a/3b) and is now
  tested *discriminatingly* (the tests fail if the guard is removed).
- **sec-1 preserved:** the platform refresh token lives in module memory only, never
  localStorage — so XSS cannot exfiltrate a 7-day high-privilege credential.
- **Deliberate non-goal — hard-refresh re-login:** because nothing is persisted, a reload
  has no credential to rehydrate from. This is the intended posture (documented in
  `FIX_BEFORE_PROD.md` AUD-14), **not** a defect — do not "fix" it by persisting tokens.

## 8. Known gaps / follow-ups (tracked)

- **Pre-existing test-infra flake (not PC-02):** `tests/identity/conftest.py` truncates on
  *teardown* only, so the first test of a run collides when the dev DB holds the demo seed.
  Re-running (or a clean DB, as CI uses) is green. Candidate fix: truncate on **setup** too.
- Next: **PC-03** (governance model + per-company detail screen + suspend/reactivate).
