# EPIC PC-05 — Break-glass support access (request → customer consent → time-boxed → logged)

| Field | Value |
|---|---|
| **Epic ID** | PC-05 |
| **Module** | Platform Console (`PC`) |
| **Status** | ✅ **Done** — grant lifecycle (PC-05a) + the platform request panel & company HITL approval inbox (PC-05b) |
| **Branch** | `feat/platform-break-glass` (off `main`, which now has PC-01…PC-04) |
| **PR** | PR-5 (backend: grant lifecycle, both auth domains, audit emission) |
| **Depends on** | PC-04 (`audit_log` — every transition writes here); the Identity auth domains |
| **Enables** | The consented access path any future **content** feature (Connect/Ask/Learn) must gate on |
| **Closes (FIX_BEFORE_PROD)** | "Add a break-glass + audit mechanism if support access is ever needed" |
| **Review** | [docs/audits/2026-06-01_platform-break-glass-pr5-review.md](../../audits/2026-06-01_platform-break-glass-pr5-review.md) |
| **Date** | 2026-06-01 |

## 1. Goal & context

Give One AI a **break-glass** path: the day support genuinely needs into a tenant, it must
be **explicit, customer-consented, time-boxed, and fully logged** — never an ambient
capability bolted onto the platform-admin role. A platform admin **requests** access to one
company with a reason; a **company_admin of that company must approve** it; approval opens a
**time-boxed** window; everything is **logged** to the PC-04 `audit_log`. This is the operator
side of the trust architecture — "can your staff get into our data?" has the structural answer
"only if we say yes, for a bounded time, and we see every step."

> **Content-blindness is preserved.** A grant is a **consent/lifecycle record**, not a key:
> there are no tenant-content endpoints yet, so an *active* grant unlocks **nothing** today.
> `support_grant_view.grant_is_active(...)` is the **seam** every future content read must gate
> on — until then break-glass is the machinery, ready, granting no access.

## 2. Scope

**In scope (PC-05a — backend):**
- `support_grant` table (migration `0006`) — tenant-tagged (`org_id`), **no FKs** (durable,
  like `audit_log`), `status` CHECK `requested|approved|denied|revoked` (**no stored
  `expired`** — expiry is computed live against `expires_at`).
- A platform service (request / list-mine / revoke) + a company service (inbox / approve /
  deny / revoke), each on its own session/domain.
- Endpoints: platform `POST /platform/orgs/{id}/support-requests`, `GET
  /platform/support-requests`, `POST /platform/support-requests/{id}/revoke`; company `GET
  /support-access`, `POST /support-access/{id}/approve|deny|revoke`.
- Audit emission to `audit_log`: `support.requested/approved/denied/revoked`
  (`support.approved` carries `expires_at` in `details`).

**Out of scope (PC-05b — UIs):** the platform "request access" control on the org detail
screen + the company **approval inbox** (the Human-in-the-Loop `animate-clari-pulse`
affordance). Also out: notifications (the inbox is **pull-only** today) and the actual
content-access gate (a forward hook — no content exists yet).

## 3. User stories

| ID | Story |
|---|---|
| PC-05-S1 | As a platform admin, I can **request** time-boxed access to one company, with a reason. |
| PC-05-S2 | As a company_admin, I can **see and approve/deny** support requests targeting **my** company. |
| PC-05-S3 | As a company_admin, I can **revoke** an approved grant to cut off access early. |
| PC-05-S4 | As the system, an approved grant is **time-boxed** and **every step is logged** — and access is decided by the clock, not a stored flag. |
| PC-05-S5 | As a company, **no One AI staff path can self-approve** access to us — our consent is mandatory. |

## 4. Acceptance criteria → tests (traceability matrix)

> ⭐ = security/consent-critical. BE = `backend/tests/identity/routes/test_support_routes.py`.

| AC | Criterion | Proven by |
|---|---|---|
| ✅ PC-05-AC1 | A platform admin requests access (`requested`); the request records the **denormalized requester email** (informed consent). | `::test_full_lifecycle_request_then_company_approves`, `::test_request_creates_requested_not_approved_consent` |
| ✅ ⭐ PC-05-AC2 | **Consent:** no platform path produces `approved`; only the company approve endpoint does, and it sets the time box + records the decider. | `::test_request_creates_requested_not_approved_consent`, `::test_full_lifecycle_request_then_company_approves` |
| ✅ ⭐ PC-05-AC3 | **Cross-tenant isolation:** a company_admin sees/decides **only their org's** grants — another org's grant is invisible (empty inbox) and approving it → **404**, untouched. | `::test_company_inbox_is_org_scoped`, `::test_cross_tenant_approve_returns_404` |
| ✅ ⭐ PC-05-AC4 | **State machine:** illegal transitions → **409** (approve a non-`requested` grant; revoke a `denied`/`revoked` one). | `::test_approve_twice_returns_409`, `::test_deny_then_revoke_returns_409` |
| ✅ ⭐ PC-05-AC4b | **No lost updates:** concurrent transitions on the same grant **serialize** (`SELECT … FOR UPDATE`) — a revoke can't be silently overwritten back to `approved`. | `::test_concurrent_transitions_serialize_via_row_lock` (review fix) |
| ✅ PC-05-AC5 | A platform admin can revoke **their own** request; another admin's grant → 404 (requester-scoped). | `::test_platform_cannot_revoke_another_admins_grant` |
| ✅ ⭐ PC-05-AC6 | **Audience confinement:** a company token is rejected on the platform request endpoint and a platform token on the company approve endpoint (both 401). | `::test_company_token_rejected_on_platform_request`, `::test_platform_token_rejected_on_company_approve` |
| ✅ PC-05-AC7 | **Live expiry:** an approved grant past `expires_at` reads `is_active=false` though `status` stays `approved` (the clock decides). | `::test_expiry_is_computed_live_from_expires_at` |
| ✅ PC-05-AC8 | Every transition is **logged**; `support.approved` carries `expires_at` in `details` ("logged → expire"). | `::test_approve_emits_audit_event_with_expires_at` |
| ✅ PC-05-AC9 | Frontend: platform request control (on the org detail screen) + company approval inbox (HITL, `animate-clari-pulse`). | FE `support/SupportInbox.test.tsx::test_shows_pending_request_with_requester_and_reason`, `::test_approve_flips_to_active_with_revoke`; `platform/OrganizationDetailPage.test.tsx::test_renders_the_support_access_panel` |

## 5. Implementation map

| Area | Files |
|---|---|
| Schema | `models/support_grant.py`, migration `0006_support_grant.py`, `enums.py` (`SupportGrantStatus`) |
| Read model / seam | `services/support_grant_view.py` (`grant_is_active` = the content-gate seam, `to_support_response`) |
| Services | `services/platform_support_service.py` (request/list/revoke), `services/company_support_service.py` (inbox/approve/deny/revoke) |
| Data access | `repositories/support_grant_repository.py` (`get_in_org` = company boundary, `get_for_requester` = platform boundary) |
| API | `routes/support_routes.py` (two routers), `schemas/support_schemas.py`, `dependencies.py`, `router.py` |
| Errors | `exceptions.py` (`SupportGrantNotFoundError`→404, `InvalidGrantTransitionError`→409) + `error_handlers.py` |
| Audit | `services/audit_service.py` (`support.*` actions) |

## 6. Decisions settled during the PR

- **Compute `is_active` live; no stored `expired`, no sweeper.** A grant is active iff
  `status='approved' AND now < expires_at`. The access check reads the clock, never a column,
  so they can't disagree; "logged → expire" is satisfied by putting `expires_at` in the
  `support.approved` audit `details`. (Rejected: lazy materialization — a write-during-read
  with a duplicate-`support.expired` race.)
- **Two service guards ARE the feature** (built + tested first): cross-tenant `get_in_org`
  (404, no existence leak) and per-transition status checks (409). A naive `status='approved'`
  with no current-state check is the bug that lets a revoked grant be re-approved.
- **Transitions serialize via a row lock** (added in review): the transition loaders
  `SELECT … FOR UPDATE`, so two concurrent privileged actors can't lost-update one another (a
  revoke racing an approve must stick). The list reads stay non-locking. Mirrors the DYN-01
  last-admin lock. `support_grant` also carries an inert `org_isolation` RLS policy (migration
  `0006`, like `0003`); the platform side is the cross-org BYPASSRLS exception (login/onboard).
- **Consent is structural,** not a flag: approval lives only on the company router; the
  platform service has no approve method. Pinned by AC2.
- **Denormalized emails looked up at write time** (requester + decider) — informed consent
  needs *who* is asking, and the record stays attributable after the actor is deleted.
- **Tenant-tagged, platform plain-session access** is the documented cross-org exception
  (same as login/onboard); `support_grant` has no FKs so it survives erasure (PC-06).

## 7. Remaining + notes

- ✅ **UIs (AC9) — DONE (PC-05b):** the `frontend/src/support/` module — a "Request access"
  control on the org detail screen (`SupportAccessPanel`) + the company **approval inbox**
  (`SupportInbox`, HITL `animate-clari-pulse` glow on pending requests, on the home for a
  company_admin). The inbox is **self-effacing**: any fetch error just hides the widget, never
  tears down the session (it's a secondary panel on an already-authenticated home).
- **Pull-only inbox:** no notification to the company_admin yet — a real-time/badge signal is
  the next refinement.
- **Content gate (forward hook):** `grant_is_active` is the seam a future content read must
  call; today it gates nothing because no content endpoint exists.
- **Dynamic QA (Target 07):** an adversarial live pass over the grant lifecycle to follow.
