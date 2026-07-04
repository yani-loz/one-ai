# EPIC CO-01 — Connector Authorization: Entitlement → Governance → Self-Connect

| Field | Value |
|---|---|
| **Epic ID** | CO-01 |
| **Module** | Connectors (`CO`) |
| **Status** | 📝 Planned / spec (design agreed; not built) |
| **Branch / commit** | _tbd_ (`feat/connector-authorization`) |
| **PR** | Phased (see §9) — Phase 1 first |
| **Depends on** | Connectors module (IMAP ingest, `connector_connection`, SyncRunner, erasure hooks — all built); Identity (`require_company_admin`, `get_current_principal`, platform-admin plane); RLS role split (0009) |
| **Closes / advances** | The authorization layer over the (built) connector data plane; advances the §7 boundary + closes part of the CA-01 admin-set-credential impersonation gap |
| **Defers to** | Phase 2 (OAuth/xoauth2), Phase 3 (platform-entitlement UI + billing seam) — see §9; per-user audit-view → CA-02 |
| **Date** | 2026-06-15 |

## 1. Goal & context

Today the connector plane is **company-admin-only and org-owned**: every `/connectors/*` endpoint is `require_company_admin`, and `connector_connection` is tenant-scoped with **no per-user owner**. So the *only* way to ingest a mailbox is for an admin to type that mailbox's app-password — which, for an **employee's personal mailbox**, breaks One AI's own constraints: it puts the admin in possession of the employee's credentials (a direct path past **Project_Bible §7** "admins see knowledge, not conversations" — the same impersonation boundary already flagged for CA-01), needs no employee **consent** (GDPR / DACH Betriebsrat), and contradicts the "**personal AI per employee**" vision.

This epic adds the **authorization model** over that built data plane — a three-tier design:

| Tier | Owner | Power |
|---|---|---|
| **1 — Entitlement** | Platform/super admin (Ethera) | Grants/revokes which connector **types** a *company* may use, tied to the **paid plan**. Outermost gate. |
| **2 — Governance** | Company admin | Within the entitlement: enable/disable each type **org-wide** *and* **per individual user** (grant/deny). Sees **health/metadata only**, never credentials or content. |
| **3 — Self-connect** | Every user (incl. the admin as a user) | Connects **their own** mailbox (OAuth-first; app-password fallback) + explicit **consent**; owns it + its ingested knowledge in their personal plane; can disconnect/erase. |

**Hard invariants (carried into every slice):** (a) **nobody types another person's credentials** — everyone self-connects what they own, including the admin for `admin@`/`info@`; there is no "admin provisions an employee's mailbox" path. (b) **Entitlement = subscription.** (c) **§7 holds** — admin/platform see *connected-or-not* + sync health, never the inbox. (d) **OAuth-first**, app-password is the fallback (OAuth-incapable / shared mailboxes).

## 2. Scope

**In scope (across the phases in §9)**
- New tiered data model: `connector_entitlement` (platform/org), `connector_policy` + `connector_policy_override` (org-wide + per-user), an **owner dimension** on `connector_connection` (`owner_user_id` — NULL = org-owned/shared, set = user-owned), a `connector_consent` record, and the `xoauth2` auth method.
- A single server-side **permission-resolution** guard `can_user_self_connect(user, type, org)` (entitlement → org/per-user policy), called before any self-connect.
- API split: `POST/GET /platform/orgs/{id}/connector-entitlements` (Tier 1), `…/connectors/policies/*` (Tier 2, admin), and **`/me/connectors/*`** (Tier 3, any authenticated user, scoped to OWN connections). The current `/connectors/*` admin routes **move** to `/admin/connectors/*` (governance/health), not deleted.
- Three UI surfaces: platform entitlement toggles (on the org-detail screen), a company-admin **Connector Access** governance panel (org-wide + per-user matrix + health), and a member **"My Connections"** self-connect screen (consent → connect → manage → erase).
- **Per-user GDPR erasure** of a user's connector + ingested data (consent withdrawal, offboarding); consent capture (HITL); audit across all three tiers.

**Out of scope (tracked)**
- A real **billing/subscription** backend — `connector_entitlement` is the seam; Phase 1 stubs it (platform-admin manual / assume-entitled). → FIX_BEFORE_PROD.
- **Removing** the legacy admin app-password create path entirely (it's narrowed to org-owned/shared mailboxes; full removal gated on OAuth landing). → §10.
- **Bulk** governance actions ("deny everyone except team X"), connector types beyond IMAP, and a company-scoped connector **audit view** → CA-02.

## 3. User stories

| ID | Story |
|---|---|
| CO-01-S1 | As a **platform admin**, I grant/revoke a company's access to a connector **type** (per their plan), and a non-entitled company can't use it at all. |
| CO-01-S2 | As a **company admin**, I enable/disable a connector **org-wide** for my company (within what we're entitled to). |
| CO-01-S3 | As a **company admin**, I **grant or deny** a connector to **individual employees** (some people are excluded, or only some are included). |
| CO-01-S4 | As a **company admin**, I see which mailboxes are **connected + their sync health** — but never anyone's credentials or email content (§7). |
| CO-01-S5 | As an **employee**, I connect **my own** mailbox (OAuth, or app-password fallback) after an explicit **consent** step — and only if I'm allowed. |
| CO-01-S6 | As an **employee**, I see, sync, **disconnect**, and **erase** my own connections; I never see anyone else's. |
| CO-01-S7 | As a **company admin who is also a user**, I connect my own `admin@`/`info@` mailbox via the **same self-connect flow** (I don't "provision" it for someone else). |
| CO-01-S8 | As the **system**, no admin or platform user can ever read a user's credentials or inbox; a denied/non-entitled user cannot connect; one user cannot touch another's connection. |
| CO-01-S9 | As a **data subject (GDPR)**, withdrawing consent / being offboarded **erases** my connector + its ingested personal data. |

## 4. Acceptance criteria → tests (traceability matrix)

> Forward spec — "Proven by" lists the **planned** tests each AC will ship with (BE = `backend/tests/…`, FE = `frontend/src/…`). The cross-tenant **and** per-user negatives are non-negotiable (`testing.md`).

| AC | Criterion | Proven by (planned) |
|---|---|---|
| CO-01-AC1 | **Permission resolution** is correct across all branches: non-entitled→denied; entitled + (org-wide-on or per-user-grant) + not-denied → allowed; explicit deny wins; org-wide-off + no grant → denied. | BE `test_permission_resolution.py` (table-driven, all branches) |
| CO-01-AC2 | A **member** can create/list/test/sync/disconnect **their own** connection via `/me/connectors/*`; the route is `get_current_principal`-gated (member ok, 401 on no/expired token). | BE `test_me_connector_routes.py`; FE `MyConnectionsPage.test.tsx` |
| CO-01-AC3 | **Per-user isolation:** user A cannot read/sync/delete user B's connection (404, no existence leak) — even in the same org. | BE `test_me_connector_routes.py::test_cannot_touch_another_users_connection` |
| CO-01-AC4 | **Cross-tenant isolation:** a user/admin in org A cannot see/act on any org B connection or policy (404). | BE `test_connector_governance.py` + `test_me_connector_routes.py` (cross-org negatives) |
| CO-01-AC5 | A **denied** or **org-disabled-and-not-granted** user is refused self-connect (**403**, friendly "your administrator hasn't enabled this") before any credential/OAuth work. | BE `test_me_connector_routes.py::test_denied_user_403`; FE (allowed-types dropdown empty/hidden) |
| CO-01-AC6 | A **company admin** can set org-wide enable/disable and per-user grant/deny; an admin **cannot enable a type the org isn't entitled to** (422/403). | BE `test_connector_governance.py`; FE `ConnectorGovernancePanel.test.tsx` |
| CO-01-AC7 | A **platform admin** grants/revokes a company's entitlement; revoking hides the type from the org (policies/connections persist, re-exposed on re-grant — no surprise cascade). | BE `test_connector_entitlement.py`; FE `OrganizationDetailPage.test.tsx` (entitlement toggle) |
| CO-01-AC8 | **§7 metadata-only:** every admin/platform response is connection **metadata** (status/health/owner-id) — never secret, never content; verified field-by-field. | BE `test_connector_governance.py::test_admin_view_is_metadata_only` |
| CO-01-AC9 | **Consent** is recorded at self-connect (who/when/scope/provider/method); **withdrawal** disables sync + is retained as proof. | BE `test_connector_consent.py` |
| CO-01-AC10 | **Per-user erasure:** disconnect / offboarding deletes the user's connection(s) + ingested email/attachments/sync state + consent; org-retained data decision honored (§8). | BE `test_user_connector_erasure.py` (per-user A-erased/B-intact) |
| CO-01-AC11 | **OAuth (Phase 2):** `oauth-start`→provider→`oauth-callback` exchanges a `state`-bound code for tokens, stores them encrypted, creates a user-owned `xoauth2` connection; replayed/forged state is refused. | BE `test_oauth_self_connect.py`; FE `OAuthConsent.test.tsx` |
| CO-01-AC12 | **Audit** rows for `entitlement.granted/revoked`, `connector.policy_changed`, `connector.consented/connected/disconnected` — actor-attributed, org-scoped. | BE `test_connector_audit.py` |

## 5. Design — data model, API, permission resolution

**Data model (migration `00NN`, after the current head):**
- `connector_entitlement(org_id, connector_type, enabled, granted/revoked_by_platform_admin, …)` — **platform-scoped** (no tenant RLS; read via global role). `UNIQUE(org_id, connector_type)`. Billing seam = `enabled`/grant metadata.
- `connector_policy(org_id, connector_type, org_wide_enabled, set_by_user_id, …)` — tenant RLS (`org_isolation`). `UNIQUE(org_id, connector_type)`.
- `connector_policy_override(org_id, user_id, connector_type, override_type∈{grant,deny}, set_by_user_id, …)` — tenant RLS; composite FK `(org_id, user_id)→users(org_id, id)`; `UNIQUE(user_id, connector_type)` (one override per user/type).
- `connector_connection` **+`owner_user_id` UUID NULL** FK→`users(id)` (NULL = org-owned/shared; set = user-owned). Replace `UNIQUE(org_id, type, username)` with a **partial** unique: per-user for owned rows, per-org for shared. RLS extended: a member sees/acts on **only their own** rows; an admin sees org rows as **metadata**. `ON DELETE` of the owner cascades the connection → triggers per-user erasure.
- `connector_consent(org_id, user_id, connector_type, scope, method, granted_at, withdrawn_at, ui_proof, …)`.
- `xoauth2` added to `AuthMethod`; OAuth tokens stored in the existing encrypted credential column (`secret_ciphertext`, AES-256-GCM) — refresh on demand by the SyncRunner (Phase 2).

**API surface (3 tiers):**
| Method / path | Auth | Purpose |
|---|---|---|
| `GET/PUT /platform/orgs/{id}/connector-entitlements` | platform admin | Tier 1: grant/revoke a company's connector types |
| `GET /admin/connectors`, `/admin/connectors/{id}` (+ test/enable/disable/sync/delete) | `require_company_admin` | Tier 2: org-wide connection **health/metadata** + admin-owned (shared) lifecycle |
| `GET/PUT /admin/connectors/policies` (org-wide + per-user grant/deny) | `require_company_admin` | Tier 2: governance |
| `GET/POST/DELETE /me/connectors` (+ `/me/connectors/{id}/test|sync`, `/oauth-start`, `/oauth-callback`) | `get_current_principal` (any user, scoped to OWN) | Tier 3: self-connect, manage, erase |

The current `/connectors/*` admin routes **move** to `/admin/connectors/*` (no behavior change for the admin-as-reader); `/me/connectors/*` is new. Every `/me` self-connect call runs `can_user_self_connect(user, type, org)` first; every admin governance write checks the org is **entitled** (admins can't grant beyond entitlement).

**Permission resolution** (single guard, top-down): `entitled(org, T)` → else deny · then `override(user, T)`: `deny`→deny, `grant`→allow, else `org_wide_enabled(org, T)`.

## 6. Implementation map (requirement → planned code)

| Area | Files (new ↦ / changed →) |
|---|---|
| Data model + migration | ↦ `models/connector_entitlement.py`, `connector_policy.py`, `connector_policy_override.py`, `connector_consent.py`; → `models/connector_connection.py` (`owner_user_id`), `enums.py` (`xoauth2`), new Alembic migration + RLS |
| Permission resolution | ↦ `connectors/services/connector_authz.py` (`can_user_self_connect`) |
| API | ↦ `routes/me_connector_routes.py`, `routes/connector_governance_routes.py`, platform `connector_entitlement_routes.py`; → `routes/connector_routes.py` (move to `/admin`) + repositories/schemas |
| Consent + erasure | ↦ `services/connector_consent_service.py`, `repositories/user_connector_erasure_repository.py`; → register a **per-user** erasure hook (`common/erasure_hooks.py`) |
| OAuth (Phase 2) | ↦ `connectors/oauth/` (provider registry, token exchange/refresh), `oauth_state` model |
| Frontend — Tier 1 | → `platform/OrganizationDetailPage.tsx` (entitlement toggles) + `platformClient` |
| Frontend — Tier 2 | ↦ `admin/ConnectorGovernancePanel.tsx` (org-wide tab + per-user matrix + health) + `adminClient` |
| Frontend — Tier 3 | ↦ `me/MyConnectionsPage.tsx`, `ConsentModal.tsx`, `OAuthConnect.tsx`; → rename/repurpose `connect/ConnectorsPage`; new `/me/connections` route (any authenticated user, **not** `AdminRoute`) |
| Audit | → `identity/services/audit_service.py` (`entitlement.*`, `connector.policy_changed`, `connector.consented/disconnected`) |

## 7. Manual / QA test plan

1. **Entitlement** — as platform admin, on an org's detail screen, grant **IMAP**; revoke it → the company-admin governance panel shows "not included in your plan."
2. **Org-wide governance** — as company admin, enable IMAP **org-wide**; a member now sees it as available.
3. **Per-user override** — **deny** IMAP to member B (org-wide-on) → B can't connect; **grant** IMAP to member C while org-wide-**off** → only C can connect.
4. **Self-connect** — as member C, "My Connections" → connect own mailbox → **consent** step → (OAuth or app-password) → connection appears, syncs, shows health.
5. **§7** — as company admin, the governance/health view shows *connected ✓ / last-synced / status* for C's mailbox but **no credential, no email content**; network tab confirms metadata-only.
6. **Isolation** — member C cannot see/sync/delete member B's connection (404); org-A admin sees nothing of org-B.
7. **Admin-as-user** — the admin uses the **same** "My Connections" screen for `admin@`/`info@` (not a provisioning path).
8. **Erasure** — member C disconnects (or is offboarded) → their connection + ingested email + consent are erased; audit shows it.

## 8. Non-functional / security / GDPR / §7

- **§7 enforcement points:** admin/platform responses are `ConnectionMetadataResponse` only (status, health, `owner_user_id`, sync state) — never `secret_ciphertext`, never `email_message`/content. The **owner dimension** is the structural enforcement: only `owner_user_id == principal.subject_id` may read content-adjacent or credential-adjacent surfaces; admins/platform are restricted to metadata.
- **Consent (HITL, GDPR Art. 7):** captured at self-connect (who/when/scope/provider/method + UI proof); **withdrawal** (Art. 7(4)) disables sync immediately and is retained as proof. No sync runs without an active consent.
- **Per-user erasure (Art. 17) — erase the raw, keep the learned (§10.4):** disconnect/offboarding deletes the user's connection(s), sync state, ingested `email_message`/recipients/attachments, and consent (the **raw personal-data tier**). The **learned/derived knowledge** already folded into company memory is **retained** — it's anonymized/aggregated and not traceable to the individual (Project_Bible §13). So per-user erasure is a **targeted raw-tier delete**, not a learned-tier delete; the erasure certificate states this.
- **Credentials:** OAuth refresh tokens + app-passwords live only in `secret_ciphertext` (AES-256-GCM); disconnect revokes the OAuth grant where the provider supports it. The SSRF egress guard already covers the dial.
- **Non-negotiable tests:** cross-tenant **and** per-user negatives (AC3/AC4), the metadata-only assertion (AC8), and the permission-resolution table (AC1).

## 9. Phasing

- **Phase 1 (NOW) — model + governance + self-connect (app-password) + the Connectors panel.** Data model + `can_user_self_connect` + `/me/connectors/*` (app-password) + admin governance (org-wide + per-user) + the **Connectors panel** (§5.1: cards → detail with Status/Actions/History/Settings, IMAP only) + per-user erasure (raw-tier). **Lowest risk; delivers the whole model end-to-end** without OAuth. Entitlement stubbed (platform-admin manual / assume-entitled).
- **Phase 2 (deferred) — OAuth (xoauth2).** Provider registry (Google/Microsoft), `oauth-start`/`oauth-callback`, encrypted token storage + on-demand refresh, OAuth-first in the connect step. Narrows the legacy admin app-password create to org-owned/shared only. **Build notes + urgency captured 2026-07-03 in `docs/FIX_BEFORE_PROD.md` (CO-01 section):** M365 tenants have basic-auth IMAP disabled → app-password fallback doesn't work there at all; Google's IMAP scope is restricted (app verification + CASA assessment — long-lead, start before GA); store refresh tokens + revoke at the provider on disconnect.
- **Phase 3 — platform entitlement UI + billing seam.** Platform-console entitlement toggles wired to a real plan/subscription source; optional bulk governance actions; more connector types (Fathom/Slack/Local Folders — the archive already shows the multi-connector panel shape).

## 10. Resolved decisions (2026-06-15)

1. **Per-user GRANT is an UPGRADE (override wins over org-wide).** ✅ The common case is "200 people, only one should have it" → leave the connector **org-wide-off** and **grant** the one user. So a per-user `grant` lets a user connect even when org-wide is off; a per-user `deny` excludes a user even when org-wide is on; **entitlement (Tier 1) is the hard ceiling**. (Resolution precedence in §5 stands as written.)
2. **App-password self-connect is allowed.** ✅ A user (incl. the admin for `admin@`/`info@`) can self-connect via **app-password** — needed for OAuth-incapable/shared/older mailboxes. OAuth is offered first **once it lands**; until then app-password is the path. (Still self-connect — nobody types another's creds.)
3. **OAuth deferred — IMAP only for now.** ✅ Phase 2 (xoauth2 + provider selection) is **not built yet**; we ship the **connectors panel** for IMAP app-password self-connect first (see §5.1). When OAuth lands we add Google/Microsoft.
4. **Offboarding: erase the raw, keep the learned.** ✅ When a user leaves / withdraws, **erase their raw personal data** — the connection, their ingested `email_message`/recipients/attachments, sync state, and consent. The **learned/derived knowledge** already folded into company memory (the compounding-intelligence layer) **stays**, because it's anonymized/aggregated and **not traceable** back to the individual (Project_Bible §13). This sets the erasure scope: per-user erasure targets the raw tier, not the learned tier.
5. **Billing manual for the pilot.** ✅ Entitlement is **platform-admin-manual** in Phase 1/2; wire a real subscription/plan source in Phase 3.

## 5.1 UX — the Connectors panel (per the v2 archive pattern)

The self-connect (Tier 3) surface adopts the richer **panel → detail-with-tabs** pattern from the v2 archive (`_archive/one-ai/frontend/src/features/admin-connectors/`), not a flat add-mailbox drawer:

- **Connectors panel** — a grid of **connector cards** (one per connector *type* the user is allowed: entitled **and** governance-permitted; for the pilot just **Email (IMAP)**). Each card shows name/icon + connected? + health + last-sync + records-synced. A type the user isn't allowed is hidden (or shown locked with "your administrator hasn't enabled this").
- **Connector detail** (`/connections/:type`) — a header (icon, label, health/“Syncing…” badge, records-synced) + **tabs**:
  - **Status** — connected?/health/last-sync/totals.
  - **Actions** (a.k.a. Connection) — **connect my mailbox** (app-password now; OAuth later) → **test** → **sync** (live progress) → **disconnect** + **erase my data** (GDPR).
  - **History** — my past sync runs.
  - **Settings** — my IMAP config within admin-set bounds (host/port/SSL/username, sync-depth, blocklists, batch size, attachment cap, sync interval — the archive `ImapConnectorConfig` fields).
- **Scoping by tier:** a **member** sees only **their own** connection in these tabs. The **company admin** gets the same panel for their own `admin@`/`info@` (Tier 3) **plus** a separate `/admin` governance view (org-wide + per-user toggles + org **health-only** roll-up). All §7 invariants hold: the admin's view is metadata, never the inbox.

This refines §3/§4/§6: the Tier-3 "My Connections" screen **is** this connectors panel; the per-connector **Settings** + **Actions(Connection)** tabs are the "settings and connection option for users" the panel must expose. Reuse the archive's component shape (`connector-card`, `connector-detail-layout` tabs, `imap-settings`/`imap-actions`/`imap-status`/`imap-history`) on the current design language + the *new* per-user/owner-scoped endpoints.

---
*Spec only — nothing built or committed. Decisions §10 are locked; Phase 1 (the IMAP connectors panel + governance + per-user self-connect) is ready to start on your go.*
