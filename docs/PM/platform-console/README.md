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
| [PC-01](EPIC-PC-01-super-admin-console.md) | Super-admin console — company fleet view + onboarding | ✅ Done | `feat/platform-console` · `7cc24bc` | [audit](../../audits/2026-06-01_platform-console-pr1-review.md) |
| [PC-02](EPIC-PC-02-platform-session-hardening.md) | Platform session hardening (`/me` + refresh + logout) | ✅ Done | `feat/platform-session` · `673964a` | [audit](../../audits/2026-06-01_platform-session-pr2-review.md) |
| [PC-03a](EPIC-PC-03a-org-lifecycle.md) | Org lifecycle — status, legal hold, suspend/reactivate + detail screen | ✅ Done | `feat/platform-lifecycle` · `ee16df8` | [audit](../../audits/2026-06-01_platform-lifecycle-pr3a-review.md) |
| [PC-04](EPIC-PC-04-audit-log.md) | Append-only `audit_log` + auth/org/user emission + trail viewer | ✅ Done | `main` · `c2fa88f` | [audit](../../audits/2026-06-01_platform-audit-pr4-review.md) |
| [PC-05](EPIC-PC-05-break-glass.md) | Break-glass support access — grant lifecycle + request panel + HITL inbox | ✅ Done | `feat/platform-break-glass` | [audit](../../audits/2026-06-01_platform-break-glass-pr5-review.md) |

## Roadmap (the "full control plane")

The console is being built as a sequence of shippable PRs. PC-01/02/03a are done; the rest
are planned (each becomes its own epic when started).

| PR | Epic | Scope | Status |
|---|---|---|---|
| 1 | PC-01 | Fleet view (crest gallery) + onboard drawer + sealed framing + routing | ✅ Done |
| 2 | PC-02 | Session hardening — `/platform/me` + refresh + logout (closes AUD-14) | ✅ Done |
| 3a | PC-03a | Lifecycle — status enum, legal hold, suspend/reactivate + **detail screen** | ✅ Done |
| 3b | PC-03b | Governance posture — `organization_governance` table + posture editor | ⏳ Planned |
| 4 | PC-04 | Append-only `audit_log` + admin/AI action trail | ✅ Done |
| 5 | PC-05 | Break-glass support access (request → customer approval → time-boxed → logged → expire) | ✅ Done |
| 6 | PC-06 | Erasure (legal-hold-beats-erasure) + exportable compliance artifacts | ⏳ Planned |

## Cross-cutting invariants (every PC epic inherits these)

- **Content-blind:** no `/platform/*` endpoint returns tenant content — metadata only.
- **Audience confinement:** a company token is rejected on `/platform/*` and vice-versa.
- **Client role is UX-only:** authorization is server-enforced via the JWT.
- **Aurora design language** + accessibility (see `.claude/rules/frontend-design.md`).
