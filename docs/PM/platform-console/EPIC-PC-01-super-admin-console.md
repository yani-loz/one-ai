# EPIC PC-01 — Super-Admin Console: Company Fleet View + Onboarding

| Field | Value |
|---|---|
| **Epic ID** | PC-01 |
| **Module** | Platform Console (`PC`) |
| **Status** | ✅ Done |
| **Branch / commit** | `feat/platform-console` · `7cc24bc` (review fixes folded in) |
| **PR** | PR-1 (frontend only — zero backend change) |
| **Depends on** | Identity module (`POST /platform/login`, `GET /platform/orgs`, `POST /platform/orgs`) |
| **Closes / advances** | First slice of the "full control plane" roadmap |
| **Defers to** | PC-02 (session hardening / AUD-14), PC-03 (detail screen + lifecycle) |
| **Review** | [docs/audits/2026-06-01_platform-console-pr1-review.md](../../audits/2026-06-01_platform-console-pr1-review.md) — 22 findings, all fixed |
| **Date** | 2026-06-01 |

## 1. Goal & context

Give Ethera staff a screen to **see and provision customer companies** — the operator side
of the governance control plane — while remaining **architecturally blind to tenant
content**. The screen renders each company as a unique "apex crest" (a gallery of tenant
identities) and keeps the *"operational metadata only — content is sealed"* guarantee
in view, because for DACH mid-market buyers "who can see our data?" is a gating question.

## 2. Scope

**In scope**
- A `/platform` console listing all companies as **metadata only** (from `GET /platform/orgs`).
- Fleet summary stats, search, and status filter.
- Onboard a new company + its first `company_admin` (`POST /platform/orgs`) via a drawer.
- Role-aware routing (platform admins → console; company users → home; anon → login).
- The "sealed / metadata-only" trust affordance.

**Out of scope (later epics)**
- Per-company **detail** screen, lifecycle (suspend/offboard), governance posture → PC-03.
- Durable platform session / `/platform/me` → PC-02.
- Audit log, break-glass, erasure/export → PC-04/05/06.

## 3. User stories

| ID | Story |
|---|---|
| PC-01-S1 | As a platform admin, I see **all customer companies** as metadata cards so I can survey the estate. |
| PC-01-S2 | As a platform admin, each company has a **unique, stable visual identity** so I recognise it at a glance. |
| PC-01-S3 | As a platform admin, I can **search and filter** the fleet by name/slug and status. |
| PC-01-S4 | As a platform admin, I can **onboard a new company + its first admin** and hand off credentials. |
| PC-01-S5 | As a platform admin, duplicate slug/email is **handled gracefully** (clear message, no dead end). |
| PC-01-S6 | As the system, **only platform admins** reach the console and each role lands on its own home. |
| PC-01-S7 | As a buyer/auditor, the **content-sealed guarantee** is visible and structurally true. |

## 4. Acceptance criteria → tests (traceability matrix)

> Legend: BE = backend (`backend/tests/...`), FE = frontend (`frontend/src/...`). All
> tests pass as of this epic (FE: 79 tests at PR-1 close; BE platform suite green).

| AC | Criterion | Proven by (automated) |
|---|---|---|
| PC-01-AC1 | The list shows only the 6 metadata fields (id, name, slug, status, user_count, created_at) — **no content/cost/tokens**. | BE `test_platform_routes.py::test_list_orgs_returns_metadata_only`; FE `PlatformConsolePage.test.tsx::test_renders_company_list_and_sealed_banner` |
| PC-01-AC2 | Each company crest is **seeded from `org.id`** (stable, unique). | FE `CompanyCard.test.tsx::test_renders_name_slug_and_seats`; insignia determinism `generateInsignia.test.ts` |
| PC-01-AC3 | Search filters by name/slug; a no-match shows a **distinct** message. | FE `PlatformConsolePage.test.tsx::test_search_filters_the_list`, `::test_search_no_match_shows_distinct_message` |
| PC-01-AC4 | Onboard creates org + first `company_admin`; slug **auto-suggests**; client validation mirrors backend bounds. | FE `OnboardCompanyDrawer.test.tsx::test_slug_auto_suggests_from_company_name`, `::test_slug_stops_auto_suggesting_after_manual_edit`, `::test_invalid_slug_keeps_submit_disabled`, `::test_short_password_keeps_submit_disabled`; `onboardValidation.test.ts` (×7); BE `::test_onboard_creates_org_and_first_admin` |
| PC-01-AC5 | On success the **seed credentials** are shown once (copyable) and the list refreshes. | FE `OnboardCompanyDrawer.test.tsx::test_successful_onboard_shows_credentials_and_refreshes_on_done`, `::test_copy_button_copies_credential_and_toggles_label` |
| PC-01-AC6 | Duplicate slug/email → **409 → "already taken"**; other failures → generic message (never misleading). | FE `OnboardCompanyDrawer.test.tsx::test_duplicate_slug_or_email_shows_specific_message`, `::test_non_duplicate_failure_shows_generic_message_not_already_exists`; BE `::test_onboard_duplicate_slug_returns_409`, `::test_onboard_overlong_admin_password_returns_422` |
| PC-01-AC7 | Only platform admins reach `/platform`; company user → `/`, anon → `/login`; loading shows skeleton (no flicker). | FE `PlatformRoute.test.tsx` (×4); `App.test.tsx::routes an authenticated platform admin from / onward to the console`; `LoginPage.test.tsx::test_platform_toggle_switches_to_platform_login_endpoint_and_lands_on_console` |
| PC-01-AC8 | A lapsed session (401 while loading) **logs out** and the guard redirects. | FE `PlatformConsolePage.test.tsx::test_401_clears_the_session`; `platformClient.test.ts::test_list_surfaces_401_as_auth_error` |
| PC-01-AC9 | The **sealed / metadata-only** banner is present. | FE `PlatformConsolePage.test.tsx::test_renders_company_list_and_sealed_banner` |
| PC-01-AC10 | The onboard drawer is an **accessible modal** (focus trap, Escape, focus restore). | FE `useDialogA11y.test.tsx` (×5) |
| PC-01-AC11 | Empty fleet and error states are handled (hint / retry). | FE `PlatformConsolePage.test.tsx::test_empty_fleet_shows_onboarding_hint`, `::test_error_state_shows_retry` |

## 5. Implementation map (requirement → code)

| Area | Files |
|---|---|
| Console screen + states | `frontend/src/platform/PlatformConsolePage.tsx` |
| Company card (crest gallery) | `frontend/src/platform/CompanyCard.tsx`, `StatusBadge.tsx` |
| Onboard flow | `OnboardCompanyDrawer.tsx`, `OnboardSuccess.tsx`, `onboardValidation.ts`, `useDialogA11y.ts` |
| Sealed affordance | `SealedBanner.tsx` |
| Data client | `platformClient.ts`, `types.ts` |
| Routing / guard | `PlatformRoute.tsx`, `App.tsx` (`RoleHome`, `/platform`), `identity/LoginPage.tsx` (role nav), `identity/index.ts` (`authorizedFetch` re-export) |
| Backend (reused) | `backend/app/identity/routes/platform_routes.py` (`GET/POST /platform/orgs`) |

## 6. Manual / QA test plan

> Pre-req: stack up (`docker compose up`), demo data seeded
> (`docker compose exec backend uv run python -m scripts.seed_identity`). App at `:5173`.

1. **Fleet view** — sign in via the **Platform admin** demo button (`super@ethera.ai`).
   Expect to land on `/platform`: header `… · Platform admin`, stats **2 companies / 2
   active / 0 suspended / 4 seats**, two crest cards (Demo, Globex), the sealed banner.
2. **Search/filter** — type `globex` → only Globex shows; type `zzz` → "No companies match
   your search"; click the `Suspended` chip → empty (none suspended).
3. **Onboard** — "+ Add company" → drawer slides in. Type a name → slug auto-fills; fill
   admin name/email/password → "Onboard company". Expect the new crest to *assemble from
   particles* + a credential hand-off panel (Copy works). "Done" → the new company is in
   the list.
4. **Duplicate** — onboard again with the same slug → "already taken" message, stays open.
5. **Validation** — clear the slug to `-bad` or set a 5-char password → the button disables.
6. **Role routing** — log out → sign in as **Demo admin** (`admin@demo.oneai`) → you get the
   **company home**, not the console. Manually visiting `/platform` redirects you to `/`.
7. **A11y** — open the drawer, press **Esc** (closes); **Tab** cycles within the drawer and
   doesn't escape behind the scrim; focus returns to "+ Add company" on close.
8. **Security (sealed)** — confirm the network tab shows `GET /platform/orgs` returning only
   the 6 metadata fields — no message/conversation/memory content anywhere.

## 7. Non-functional / security

- **Content-blindness:** `GET /platform/orgs` is metadata-only (AC1); enforced + tested.
- **Role gate is UX-only:** `PlatformRoute`/`RoleHome` route on the client role, but the
  server enforces access via `aud='platform'` — a spoofed client role gets 401s from the API.
- **Design language:** aurora palette only, glass surfaces, reduced-motion honoured,
  shimmer skeletons (no spinners) — per `.claude/rules/frontend-design.md`.

## 8. Known gaps / follow-ups (tracked)

- **AUD-14** (platform session in-memory, ~15 min, no `/platform/me`) — closed by **PC-02**.
- Per-company **detail + lifecycle** (suspend/offboard/governance) — **PC-03**.
- The 22 review findings (incl. **sec-1**: platform refresh token must not be persisted) were
  all fixed in this epic — see the linked audit.
