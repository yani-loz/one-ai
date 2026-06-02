# EPIC CA-01 — Company-Admin Console: User & Access Management

| Field | Value |
|---|---|
| **Epic ID** | CA-01 |
| **Module** | Company Admin (`CA`) |
| **Status** | ✅ Done (pending commit) |
| **Branch / commit** | `feat/company-admin-console` |
| **PR** | Slice A (frontend + docs; **zero backend change** — `/users/*` already existed) |
| **Depends on** | Identity module (`GET/POST /users`, `PATCH/DELETE /users/{id}`, `require_company_admin`); break-glass company side (`/support-access`) |
| **Closes / advances** | First slice of the company-admin plane (the customer-side governance console) |
| **Defers to** | CA-02 (company-scoped audit view + `identity/company` vs `identity/platform` backend seam), CA-03 (org profile/settings + GDPR legal surface) |
| **Date** | 2026-06-02 |

## 1. Goal & context

The backend company user-management surface (`/users/*`) was built, gated, and cross-tenant-tested
in the identity module — but **headless**: a `company_admin` logged in and landed on a placeholder
home with only the break-glass inbox bolted on. This slice gives that built-but-invisible backend a
real console: a `/admin` screen where the customer's own admin manages **their** people. It is the
tenant-side counterpart to the platform console — same trust architecture, opposite side of the
boundary: people + roles + action metadata only, never Tier-1 personal intelligence and never
costs/tokens/provider names (`Project_Bible` §7/§14).

This is the surface a DACH buyer's DPO / Betriebsrat / CFO inspect, so it is **operational, not yet
compliance-complete** — RLS enforcement (the deferred role/engine flip) and a real invite/first-login
password reset gate the "production for real shared customers" claim (see §8).

## 2. Scope

**In scope**
- A `/admin` console (behind `AdminRoute`, `company_admin`-only) listing the org's users — seat
  stats (users / active / administrators), search by name/email.
- **Add a user** via a drawer (admin sets the initial password — no invite flow yet), with
  client-side validation mirroring the backend bounds (incl. the 72-UTF-8-byte password cap).
- **Change a user's role** (member ↔ administrator) inline; **deactivate** behind a confirm and
  **reactivate** a deactivated user.
- **Last-admin** protection surfaced clearly (the server's 409), and **self-eject** (demote /
  deactivate your own account) confirmed → then logged out to re-authenticate.
- The break-glass **approval inbox** relocated from the home into the console; a `company_admin`
  **entry point** added to the home so the console is reachable (no global nav yet).
- Shared `useDialogA11y` promoted to `src/components/` (one focus-trap, no divergent copy).

**Out of scope (later epics / tracked)**
- Company-scoped **audit view** + the backend `identity/company` vs `identity/platform` split → CA-02.
- **Org profile/settings** + the GDPR legal surface (subprocessor/DPA/residency, DSR path) → CA-03.
- **Email-invite / first-login password reset** (admin-set passwords today) → `FIX_BEFORE_PROD`
  ("Replace admin-set passwords…", enriched with the Tier-1 impersonation consequence).
- **RLS enforcement** (role/engine flip) — gates real shared-customer exposure → `docs/rls-jwt-enforcement-plan.md`.

## 3. User stories

| ID | Story |
|---|---|
| CA-01-S1 | As a company admin, I see **all users in my org** (incl. deactivated) so I can manage them. |
| CA-01-S2 | As a company admin, I can **add a user** (set an initial password); they appear in the list. |
| CA-01-S3 | As a company admin, I can **change a user's role** (member ↔ administrator). |
| CA-01-S4 | As a company admin, I can **deactivate** a user (confirmed) and **reactivate** them. |
| CA-01-S5 | As the system, the org **can never lose its last administrator** (server 409, surfaced clearly). |
| CA-01-S6 | As a company admin, **removing my own admin access** is confirmed and re-authenticates me. |
| CA-01-S7 | As the system, **only company admins** reach `/admin`; members/platform admins/anon are routed away. |
| CA-01-S8 | As a company admin, I **approve / deny break-glass** support access from the console. |
| CA-01-S9 | As a buyer, the console shows **people + roles only** — no conversations, costs, or tokens. |

## 4. Acceptance criteria → tests (traceability matrix)

> Legend: BE = backend (`backend/tests/...`), FE = frontend (`frontend/src/...`). All FE tests
> pass (151 total; 50 in `src/admin`); coverage 85% lines / 87% branch (floor 70%).

| AC | Criterion | Proven by (automated) |
|---|---|---|
| CA-01-AC1 | List shows my org's users, incl. inactive (no `is_active` filter on `GET /users`). | FE `AdminConsolePage.test.tsx::test_renders_user_list_after_load`; `adminClient.test.ts::test_list_returns_parsed_users_on_200`; BE `test_user_routes.py` (list) |
| CA-01-AC2 | Add user posts the payload; submit disabled until email/name/password mirror backend bounds (incl. 72-byte cap). | FE `CreateUserDrawer.test.tsx::test_submit_disabled_until_form_is_valid`, `::test_short_password_keeps_submit_disabled`, `::test_invalid_email_keeps_submit_disabled`, `::test_successful_create_calls_on_created_and_closes`; `userValidation.test.ts` (×15); `adminClient.test.ts::test_create_posts_payload_and_returns_user` |
| CA-01-AC3 | Duplicate email → **409 → "already exists"** (distinguished by call-site, not message text). | FE `CreateUserDrawer.test.tsx::test_duplicate_email_shows_specific_message`; `adminClient.test.ts::test_create_throws_409_on_duplicate_email` |
| CA-01-AC4 | Inline role change PATCHes `{role}`. | FE `AdminConsolePage.test.tsx::test_inline_role_change_patches_the_role`; `adminClient.test.ts::test_update_patches_the_role_body` |
| CA-01-AC5 | Deactivate behind a confirm → DELETE; reactivate PATCHes `{is_active:true}`. | FE `AdminConsolePage.test.tsx::test_deactivate_member_confirms_then_calls_delete`; `adminClient.test.ts::test_deactivate_sends_delete_and_resolves_on_204`, `::test_update_patches_the_is_active_body` |
| CA-01-AC6 | Last-admin guard (**409**) surfaced clearly (inline notice / dialog). | FE `AdminConsolePage.test.tsx::test_last_admin_deactivate_shows_409_in_dialog`; `adminClient.test.ts::test_update_throws_409_on_last_admin`, `::test_deactivate_throws_409_on_last_admin`; BE `test_user_service.py` (last-admin guard, concurrency) |
| CA-01-AC7 | Self-demotion is **confirmed**, then **logs out** to re-auth with reduced privileges. | FE `AdminConsolePage.test.tsx::test_self_demotion_confirms_then_logs_out` |
| CA-01-AC8 | Only `company_admin` reaches `/admin`; member/platform/anon routed away; loading → skeleton (no flicker). | FE `AdminRoute.test.tsx` (×5) |
| CA-01-AC9 | A lapsed session (**401**) logs out and the guard redirects (never a connectivity message). | FE `AdminConsolePage.test.tsx::test_401_on_load_logs_out`; `CreateUserDrawer.test.tsx::test_session_expired_calls_on_session_expired`; `adminClient.test.ts::test_list_surfaces_401_as_auth_error` |
| CA-01-AC10 | Empty fleet + error/retry states handled. | FE `AdminConsolePage.test.tsx::test_empty_list_shows_add_first_hint`, `::test_load_error_then_retry_loads_list` |
| CA-01-AC11 | Search filters by name/email. | FE `AdminConsolePage.test.tsx::test_search_filters_the_list` |
| CA-01-AC12 | Break-glass inbox renders inside the console; role pill renders for known/unknown roles. | FE `SupportInbox.test.tsx` (existing, unchanged by the move); `RoleBadge.test.tsx` (×3) |
| CA-01-AC13 | **Cross-tenant isolation:** a company admin cannot read/mutate another org's user (404, no existence leak). | BE `test_user_routes.py` (cross-tenant negatives — the non-negotiable per `testing.md`) |

## 5. Implementation map (requirement → code)

| Area | Files |
|---|---|
| Console screen + states | `frontend/src/admin/AdminConsolePage.tsx` (presentation) + `useCompanyUsers.ts` (data + mutations) |
| Users list + per-row actions | `UsersTable.tsx`, `RoleBadge.tsx` |
| Add-user flow | `CreateUserDrawer.tsx`, `userValidation.ts` |
| Confirm (deactivate / self-eject) | `ConfirmDialog.tsx` |
| Data client | `adminClient.ts`, `types.ts` |
| Routing / guard | `AdminRoute.tsx`, `App.tsx` (`/admin` route), `HomePage.tsx` (company_admin entry point), `admin/index.ts` |
| Break-glass inbox (relocated) | `frontend/src/support/SupportInbox.tsx` (now used by the console) |
| Shared a11y hook (promoted) | `frontend/src/components/useDialogA11y.ts` (was `platform/`; repointed in `OnboardCompanyDrawer`, `OrgErasurePanel`) |
| Backend (reused, unchanged) | `backend/app/identity/routes/user_routes.py`, `services/user_service.py`, `schemas/user_schemas.py` |

## 6. Manual / QA test plan

> Pre-req: stack up (`docker compose up`), demo seeded
> (`docker compose exec backend uv run python -m scripts.seed_identity`). App at `:5173`.

1. **Reach the console** — sign in as the **Demo admin** (`admin@demo.oneai`). On the home, a
   **"Manage organisation"** button appears (company_admin only). Click it → `/admin`.
2. **List + stats** — see the org's users (admin + member), stats **2 users / 2 active / 1
   administrator**. The break-glass inbox sits below (empty unless a request is pending).
3. **Add a user** — "+ Add user" → drawer. The button stays disabled until name + a valid email +
   an 8+ char password; a 5-char password keeps it disabled. Add → the user joins the list.
4. **Duplicate** — add again with the same email → "A user with this email already exists."
5. **Role change** — flip the member to **Administrator** via the row dropdown → stats update.
6. **Last admin** — try to deactivate / demote the *only* administrator → "must keep at least one
   administrator" (the server 409, surfaced).
7. **Self-eject** — demote **yourself** to member → a confirm appears; confirm → you're signed out
   (re-auth as a member; `/admin` now redirects you home).
8. **Deactivate / reactivate** — deactivate a member (confirm) → row shows "Deactivated" +
   Reactivate; reactivate restores them.
9. **Role routing** — as a **member** or by URL, visiting `/admin` redirects to `/`; signed out, to
   `/login`.
10. **Privacy** — confirm the network tab shows `/users` returning only id/email/name/role/
    is_active/org_id/created_at — no conversation content, no costs/tokens.

## 7. Non-functional / security

- **Tenant scope is server-side:** the org comes from the JWT; `AdminRoute`/role checks are UX-only.
  A spoofed client role reaches the shell but every `/users` call is `require_company_admin`-gated
  (member → 403) and org-scoped (cross-org → 404). The mandatory cross-tenant negatives live in
  `backend/tests/identity/routes/test_user_routes.py`.
- **Content-blind to personal data:** people + roles + action metadata only (§7/§14).
- **Design language:** aurora palette, glass surfaces, shimmer skeletons (no spinners),
  reduced-motion honoured, accessible modals (focus-trap/Escape/restore via `useDialogA11y`).
- **Self-eject safety:** a self-targeting privilege reduction confirms, then logs out, so the admin
  re-authenticates with the new role instead of a stale-role limbo.

## 8. Known gaps / follow-ups (tracked)

- **Admin-set passwords (impersonation boundary)** — the admin sets the user's initial password and
  therefore *knows* it; once the employee plane carries Tier-1 personal intelligence, that is an
  impersonation path past the §7 "admins can't see conversations" boundary. Tracked in
  `docs/FIX_BEFORE_PROD.md` ("Replace admin-set passwords…", enriched). Fine for pilot; add forced
  first-login reset before the employee plane ships.
- **RLS still inert** — `/users/*` rides on JWT org-scoping + green cross-tenant negatives today; the
  role/engine flip (`docs/rls-jwt-enforcement-plan.md`) gates real shared-customer exposure. This
  plane raises that flip's priority (it's where a leak would bite) but is not blocked by it.
- **No company-scoped audit view yet** → CA-02 (note: `audit_log` is deliberately NOT RLS-scoped, so
  that read is guarded solely by an app-layer `WHERE org_id = principal.org_id` + its negative test).
- **No global nav** — `/admin` is reached via the home entry point only (a nav shell is a follow-up).
- **`identity/` is becoming a god-module** — the backend `identity/company` vs `identity/platform`
  sub-package split lands with CA-02's audit route (split with a reason, not preemptively).
