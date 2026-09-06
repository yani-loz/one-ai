# Module: Company Admin (`CA`)

The **tenant-side governance plane** — where a customer's own `company_admin` manages *their*
organisation: its people, their roles, and the break-glass access requests from One AI staff.
It is the company-facing counterpart to the Platform Console (`PC`): same trust architecture,
opposite side of the boundary. A company admin sees **people and metadata** — never Tier-1
personal intelligence (conversations / profiles), and never costs / tokens / provider names
(those are platform-only). It is the surface a DACH buyer's DPO / Betriebsrat / CFO inspect.

- **Frontend:** `frontend/src/admin/` (+ the `/admin` route and a company_admin entry point on
  the home). The break-glass approval inbox (`src/support/SupportInbox`) lives here.
- **Backend (reused):** the company user-management domain in `backend/app/identity/`
  (`/users/*`, `require_company_admin`) and the company side of break-glass (`/support-access`).
- **Product source:** `docs/Project_Bible.md` §7 (privacy tiers), §13 (access security / RBAC),
  §14 (customer admin panel — no costs/tokens/providers).

## Epics

| Epic | Title | Status | Branch / commit | Review |
|---|---|---|---|---|
| [CA-01](EPIC-CA-01-user-management-console.md) | Company-admin console — user & access management UI | ✅ Done | `main` · `fd944d0` (+ `db91bdc` follow-up) | _pending_ |

## Roadmap (the company-admin plane)

Built as a sequence of shippable slices, mirroring how the platform console was built.

| Slice | Epic | Scope | Status |
|---|---|---|---|
| A | CA-01 | Console shell + user CRUD UI (over the built `/users/*`) + break-glass inbox relocated + home entry point | ✅ Done (`fd944d0`) |
| B | CA-02 | Company-scoped audit view (`GET /audit`, app-layer org filter — note: since migration `0013_least_privilege_grants`, `audit_log` **is** ENABLE+FORCE RLS with the standard `org_isolation` policy, so the app-layer filter is no longer the only scope) + the `identity/company` vs `identity/platform` backend seam | ⏳ Planned |
| C | CA-03 | Org profile / settings + the GDPR legal surface (subprocessor / DPA / residency disclosure + data-subject-request path) | ⏳ Planned |

> Deferred as their own epics (not "admin"): connectors (the Connect epic), usage/learning
> stats (needs the Learn loop), SSO/MFA + password policy, HITL trust-dial, billing.

## Cross-cutting invariants (every CA epic inherits these)

- **Tenant-scoped:** the org is derived from the verified JWT (`principal.org_id`), never from
  path/body; a cross-org target resolves to **404**, never a 200-empty existence leak.
- **Content-blind to personal data:** people + roles + action metadata only — never Tier-1
  conversations/profiles, never costs/tokens/provider names (§7 / §14).
- **Client role is UX-only:** `AdminRoute` gates on the client role, but authorization is
  server-enforced (`require_company_admin` → a member's token gets 403).
- **Aurora design language** + accessibility (see `.claude/rules/frontend-design.md`).
