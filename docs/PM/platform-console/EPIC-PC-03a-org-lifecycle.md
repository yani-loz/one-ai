# EPIC PC-03a — Org Lifecycle: status, legal hold, suspend/reactivate + detail screen

| Field | Value |
|---|---|
| **Epic ID** | PC-03a |
| **Module** | Platform Console (`PC`) |
| **Status** | ✅ Done (pending commit of the frontend half) |
| **Branch** | `feat/platform-lifecycle` (stacked on `feat/platform-session`) |
| **PR** | PR-3a (the first half of the PR-3 roadmap item; governance posture table → PC-03b) |
| **Depends on** | PC-01 (the console), PC-02 (the platform session), the Identity auth domain |
| **Review** | [docs/audits/2026-06-01_platform-lifecycle-pr3a-review.md](../../audits/2026-06-01_platform-lifecycle-pr3a-review.md) — 7 findings, all fixed |
| **Date** | 2026-06-01 |

## 1. Goal & context

Give the platform admin the first **lifecycle controls** over a company — see one tenant's
detail, **suspend / reactivate** it, and place a **legal hold** — from a dedicated detail
screen. Suspension is the meaningful one: it **blocks that org's company logins** without
deleting any data. Governance *posture* (region, residency, DPA, works-council, EU AI Act
risk, retention) is scoped to **PC-03b**; here those appear as labelled slots.

## 2. Scope

**In scope:** `OrganizationStatus` enum + CHECK; `legal_hold` column (migration `0004`);
`GET /platform/orgs/{id}`, `PATCH …/status`, `PATCH …/legal-hold`; suspend-blocks-login (+
refresh); the `/platform/orgs/:id` full-screen detail route.

**Out of scope:** the `organization_governance` table + posture editor → **PC-03b**.
`offboarded` access-cutoff → **PC-06** (erasure). Audit logging of these actions → **PC-04**.

## 3. User stories

| ID | Story |
|---|---|
| PC-03a-S1 | As a platform admin, I can open one company's **detail** and see its metadata + legal-hold. |
| PC-03a-S2 | As a platform admin, I can **suspend** a company (blocking its logins) and **reactivate** it. |
| PC-03a-S3 | As a platform admin, I can place / clear a **legal hold**. |
| PC-03a-S4 | As the system, a **suspended** org's company logins **and refreshes** are blocked, without leaking suspension to an attacker. |

## 4. Acceptance criteria → tests (traceability matrix)

> ⭐ = security-critical. BE = `backend/tests/identity/routes/`, FE = `frontend/src/platform/`.

| AC | Criterion | Proven by |
|---|---|---|
| PC-03a-AC1 | `status` is pinned to the enum (invalid → 422); suspend↔reactivate works. | BE `test_platform_routes.py::test_patch_org_status_invalid_value_returns_422`, `::test_patch_org_status_suspend_then_reactivate` |
| PC-03a-AC2 | Detail returns **metadata only** (7 fields incl. legal_hold); unknown id → 404. | BE `::test_get_org_detail_returns_metadata_and_legal_hold`, `::test_get_org_detail_unknown_returns_404` |
| ⭐ PC-03a-AC3 | A **suspended** org blocks **login and refresh** (403), and the 403 is reachable **only with valid credentials** (wrong password → generic 401, no oracle). | BE `test_auth_routes.py::test_login_suspended_org_returns_403`, `::test_login_suspended_org_wrong_password_stays_401_no_oracle`, `::test_refresh_blocked_after_org_suspended` |
| ⭐ PC-03a-AC4 | `/auth/me` still **200** for a valid token under suspension (the deliberate asymmetry). | BE `test_auth_routes.py::test_me_with_valid_token_succeeds_even_when_org_suspended` |
| ⭐ PC-03a-AC5 | The PATCH write **reaches the auth gate** end-to-end (suspend via endpoint → login 403 → reactivate → login 200). | BE `test_platform_routes.py::test_patch_status_endpoint_drives_the_login_gate_end_to_end` |
| ⭐ PC-03a-AC6 | A **company token** is rejected (exactly 401) on all three new endpoints (discriminating, real-admin sub). | BE `::test_get_org_detail_with_company_token_is_rejected`, `::test_patch_org_status_with_company_token_is_rejected`, `::test_patch_org_legal_hold_with_company_token_is_rejected` |
| PC-03a-AC7 | Legal hold sets + **persists** (read-back). | BE `::test_patch_org_legal_hold_sets_flag` (with a fresh GET read-back) |
| PC-03a-AC8 | The detail screen renders metadata + governance slots + the sealed banner; cards **link** to it. | FE `OrganizationDetailPage.test.tsx::test_renders_detail_with_governance_slots_and_sealed_banner`; `CompanyCard.test.tsx::test_links_to_the_detail_screen` |
| PC-03a-AC9 | Suspend/reactivate + legal-hold toggles work from the UI and re-render from the server. | FE `OrganizationDetailPage.test.tsx::test_suspend_action_flips_to_reactivate`, `::test_legal_hold_toggles_on` |
| PC-03a-AC10 | Back navigation, error+retry, and 401→logout are handled. | FE `::test_back_navigates_to_the_fleet`, `::test_load_error_shows_retry`, `::test_401_clears_the_session` |

## 5. Implementation map

| Area | Files |
|---|---|
| Schema | `models/organization.py` (status CHECK + legal_hold), migration `0004_org_lifecycle.py`, `enums.py` (OrganizationStatus) |
| Service + API | `services/platform_org_service.py`, `routes/platform_routes.py`, `repositories/organization_repository.py` (get_with_user_count), `schemas/platform_schemas.py`, `dependencies.py` |
| Suspend gate | `services/auth_service.py` (`_load_loginable_org`; login + refresh) + `exceptions.py` / `error_handlers.py` (OrganizationSuspendedError → 403) |
| Detail screen | `platform/OrganizationDetailPage.tsx`, `CompanyCard.tsx` (Link), `platformClient.ts`, `format.ts`, `types.ts`, `App.tsx` (route) |

## 6. Manual / QA

1. Sign in as platform admin → click a company card → land on `/platform/orgs/:id`.
2. **Suspend access** → status flips; in another session a user of that org **cannot log in** (403);
   **Reactivate** → login works again.
3. **Place / Clear hold** toggles; the value persists across a reload.
4. Back returns to the fleet (reverse slide).

## 7. Known gaps / follow-ups

- **PC-03b**: `organization_governance` table + posture editor (the slots are placeholders now).
- **Test-file size:** `test_platform_routes.py` is at 468 lines — split into auth-session vs
  org-management test files soon (soft-warn; under the 500 hard ceiling).
- Audit logging of suspend/legal-hold actions → **PC-04** (`audit_log`).
