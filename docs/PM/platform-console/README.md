# Module: Platform Console (`PC`)

The **super-admin / governance control plane** — where One AI (Ethera) staff provision,
monitor, and lifecycle-manage customer companies (tenants) **without ever reading tenant
content**. It is the operator side of the trust architecture: data-minimised by design, so
"can your staff see our data?" has a structural answer (no), not just a policy answer.

- **Frontend:** `frontend/src/platform/` (+ the `/platform` route and role-aware login).
- **Backend:** the platform auth domain in `backend/app/identity/` (`/platform/*`),
  separate from company auth by JWT audience (`aud='platform'` vs `'company'`).
- **Product source:** `docs/Project_Bible.md` §13/§186/§189 (security, sovereignty, audit);
  research in `02_Research/ICP/` + `02_Research/Competitive Analysis/`.

## Epics

| Epic | Title | Status | Branch / commit | Review |
|---|---|---|---|---|
| [PC-01](EPIC-PC-01-super-admin-console.md) | Super-admin console — company fleet view + onboarding | ✅ Done | `main` · `7cc24bc` | [audit](../../audits/2026-06-01_platform-console-pr1-review.md) |
| [PC-02](EPIC-PC-02-platform-session-hardening.md) | Platform session hardening (`/me` + refresh + logout) | ✅ Done | `main` · `673964a` | [audit](../../audits/2026-06-01_platform-session-pr2-review.md) |
| [PC-03a](EPIC-PC-03a-org-lifecycle.md) | Org lifecycle — status, legal hold, suspend/reactivate + detail screen | ✅ Done | `main` · `ee16df8` (backend) + `529b7a9` (frontend) | [audit](../../audits/2026-06-01_platform-lifecycle-pr3a-review.md) |
| [PC-04](EPIC-PC-04-audit-log.md) | Append-only `audit_log` + auth/org/user emission + trail viewer | ✅ Done | `main` · `c2fa88f` | [audit](../../audits/2026-06-01_platform-audit-pr4-review.md) |
| [PC-05](EPIC-PC-05-break-glass.md) | Break-glass support access — grant lifecycle + request panel + HITL inbox | ✅ Done | `main` · `806a569` (backend) + `d4df054` (UIs) | [audit](../../audits/2026-06-01_platform-break-glass-pr5-review.md) |
| [PC-06](EPIC-PC-06-erasure.md) | GDPR erasure (legal-hold-beats-erasure) + compliance export + UI | ✅ Done | `main` · `8e9c531` (backend) + `81a8655` (UI) | [audit](../../audits/2026-06-01_platform-erasure-pr6-review.md) |

## Roadmap (the "full control plane")

The console was built as a sequence of shippable PRs. **PC-01, PC-02, PC-03a, PC-04, PC-05 and
PC-06 are all done and on `main`** (`7cc24bc` · `673964a` · `ee16df8`+`529b7a9` · `c2fa88f` ·
`806a569`+`d4df054` · `8e9c531`+`81a8655`). Only **PC-03b** is still planned: no
`organization_governance` table or code exists (verified 2026-09-06), and
`OrganizationDetailPage.tsx:41,208` renders the posture slots as placeholders "configured in PC-03b".

| PR | Epic | Scope | Status |
|---|---|---|---|
| 1 | PC-01 | Fleet view (crest gallery) + onboard drawer + sealed framing + routing | ✅ Done |
| 2 | PC-02 | Session hardening — `/platform/me` + refresh + logout (closes AUD-14) | ✅ Done |
| 3a | PC-03a | Lifecycle — status enum, legal hold, suspend/reactivate + **detail screen** | ✅ Done |
| 3b | PC-03b | Governance posture — `organization_governance` table + posture editor | ⏳ Planned |
| 4 | PC-04 | Append-only `audit_log` + admin/AI action trail | ✅ Done |
| 5 | PC-05 | Break-glass support access (request → customer approval → time-boxed → logged → expire) | ✅ Done |
| 6 | PC-06 | Erasure (legal-hold-beats-erasure) + exportable compliance artifacts | ✅ Done |

## Cross-cutting invariants (every PC epic inherits these)

- **Content-blind:** no `/platform/*` endpoint returns tenant content — metadata only.
- **Audience confinement:** a company token is rejected on `/platform/*` and vice-versa.
- **Client role is UX-only:** authorization is server-enforced via the JWT.
- **Aurora design language** + accessibility (see `.claude/rules/frontend-design.md`).
