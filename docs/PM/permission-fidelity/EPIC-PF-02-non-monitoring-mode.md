# EPIC PF-02 — Non-Monitoring Mode (structural Leistungs-/Verhaltenskontrolle exclusion)

| Field | Value |
|---|---|
| **Epic ID** | PF-02 |
| **Module** | Permission Fidelity (`PF`) |
| **Status** | 📋 **Spec** — not started (this document is the build contract) |
| **Branch** | `feat/pf-non-monitoring-mode` (planned, off `main`) |
| **PR** | — |
| **Depends on** | PC-03a (org lifecycle — settings hang off the org), PC-04 (`audit_log` — purpose-limitation rules defined here), CA-01 (company-admin console — the surfaces this epic constrains), migration 0009 RLS roles (new tenant table inherits the standing invariants) |
| **Closes (FIX_BEFORE_PROD)** | None closed; **adds** two entries: (1) monitoring-surface registry must be re-audited before every prod release, (2) Learn-layer promotion pipeline (Step 7) must wire the `behavioral`-category block before going live |
| **Review** | — (spec-stage; adversarial review to be linked at PR time) |
| **Date** | 2026-06-09 |

## 1. Goal & context

Make **"One AI does not monitor your employees"** a *structural product guarantee* — not a
policy promise — and hand the DACH buying committee (specifically the **Betriebsrat / works
council**) a machine-verifiable artifact proving it. The §87(1) Nr. 6 BetrVG objection
("technische Einrichtung zur Überwachung von Verhalten oder Leistung der Arbeitnehmer") is the
single biggest gatekeeper veto in our ICP (DACH Mittelstand, 100–500 employees, works council
present in essentially every target account). Competitors answer it with PDFs; we answer it
with **schema absence + a standing automated audit test**.

The guarantee, as a product statement:

1. **No per-employee behavioral profiling.** The system never builds, stores, or derives a
   profile of *how an employee behaves* for anyone other than that employee (Tier-1 personal
   adaptation remains 100 % private to the user — it is a feature *for* the employee, with a
   hard boundary against admin access, per the three-tier privacy model).
2. **No Leistungs-/Verhaltenskontrolle surface.** There is no screen, endpoint, export, or
   report through which an employer, manager, or admin can observe an individual employee's
   performance or behavior. Not "disabled" — **absent**.
3. **No per-user productivity metrics.** No response times, activity scores, usage rankings,
   working-hours inference, idle detection, sentiment scoring, leaderboards, or comparative
   per-employee statistics — these are *never computed*, not merely hidden.
4. **No cross-employee behavioral promotion.** The future Learn layer (Nightly Board, Step 7)
   may promote *work-product knowledge* ("the Müller contract renewal is in folder X") into
   organizational memory, but never *behavioral observations about a person* ("X replies
   slowly", "Y works weekends"). This guardrail is specified now so the standing invariant
   test catches violations the day the Learn layer lands.

> **The hard truth this epic confronts:** BAG case law reads §87(1) Nr. 6 as triggering on
> **objective suitability** for monitoring, *not* on intent or configuration. A system that
> ingests employee email and writes an append-only audit log is objectively *geeignet* to
> monitor — a config flag does not exempt us from co-determination, and we must never claim it
> does. The honest goal of Non-Monitoring Mode is different: **shrink the co-determination
> surface to a short, itemized, machine-verifiable list** so the Betriebsvereinbarung is
> negotiable in weeks instead of quarters, and so the works council can *re-verify* the
> guarantees on every release instead of trusting a sales deck. Every artifact this epic
> produces is **engineering input for the customer's lawyers — NOT legal advice** — and is
> labeled as such in the document itself.

## 2. Scope

**In scope (PF-02 — backend + invariants + artifact):**

- **Tenant-level flag:** new tenant table `org_settings` (migration `0013_org_settings`,
  `TenantMixin`, RLS policy, FORCE RLS — standing invariants apply) with
  `non_monitoring_mode BOOLEAN NOT NULL DEFAULT TRUE`. Default **ON** for every org.
  Turning it **off** requires: company-admin role + typed confirmation + a recorded
  `reason` + an `org_settings.non_monitoring_mode_disabled` event in the append-only
  `audit_log` (so a works council can later prove *when* and *why* it was disabled).
  Turning it back on is one click and also audited.
- **Flag-independent structural absences** (never built, in any flag state — enforced
  by the standing route/schema denylist tests, not by a guard):
  - **no** company-scoped (`aud=company`) endpoint emitting **per-user** usage, cost,
    activity, or interaction breakdowns is built — the INV route-enumeration test is a
    **denylist**: any company-scoped route emitting per-user usage data → FAIL, flag
    state irrelevant;
  - the (future) Learn-layer promotion path blocks any candidate fact tagged
    `category='behavioral'` at the pipeline gate **unconditionally** (same
    flag-independence as the schema denylist), logged as suppressed;
  - no export/report path joins `audit_log` to analytics — audit data is reachable
    **only** via the PC-06 compliance export, never via dashboards.
- **What the flag actually governs** (the only employer-facing dial): **aggregate
  granularity**. Flag ON: k-anonymity threshold **k ≥ 5** on customer-facing rollups
  (groups smaller than 5 users are suppressed, not shown as small groups). Flag OFF:
  small-group suppression relaxes (counts below k = 5 may appear in aggregates) —
  **nothing employer-facing becomes per-user in either state**. The
  `require_non_monitoring_guard` dependency (single choke point, `get_tenant_session`
  pattern — never per-route ad hoc) is reserved for the genuinely unavoidable per-user
  surfaces (user management, auth administration), not for analytics.
- **Never-collected vs aggregate-only data contract** (§ below) encoded as a
  **monitoring-surface registry**: `backend/app/common/monitoring_registry.py` — an
  explicit allowlist of every per-user-keyed table with its documented purpose, legal
  basis hint, and access scope. Anything per-user **not** in the registry is a test
  failure, not a review comment.
- **Monitoring-surface audit (standing test):**
  `backend/tests/invariants/test_monitoring_surface_invariants.py` — dynamically
  enumerates the live schema + route table on every test run (FAILs, never SKIPs —
  same discipline as `test_rls_invariants.py`).
- **Betriebsrat checklist artifact:** `GET /company/compliance/betriebsrat-checklist`
  (company-admin) returns a versioned JSON/Markdown document: the itemized guarantee
  list (§ below), the org's current flag state, the registry contents, and the latest
  invariant-test status — prominently headed *"Engineering input for legal review.
  Not legal advice. Does not replace co-determination under BetrVG."*

**Out of scope (later):**

- The Betriebsvereinbarung **template** itself and any DPIA — lawyer work-product; we
  supply inputs only.
- The Learn layer / Nightly Board implementation (Step 7) — only its **guardrail
  contract** is fixed here so PF-02's invariant test is already waiting for it.
- Per-user **cost tracking** (`cost_events` exists only in the `one-ai - v2` prototype,
  not in this backend) — when lifted, its rows MUST use a pseudonymous user reference,
  be registered in the monitoring registry (purpose: billing/abuse), and surface to
  customer admins as aggregates only. Tracked as a registry-entry obligation, not built
  here.
- Frontend admin UI for the flag (thin CA-01 follow-up).
- Per-user GDPR erasure (separate track; org-level erasure wiring IS in scope, see AC9).

## 3. User stories

| ID | Story |
|---|---|
| PF-02-S1 | As a **works council member**, I can read an itemized checklist of what the system can and cannot observe about employees, and re-verify it against a live automated audit on every release — not trust a vendor PDF. |
| PF-02-S2 | As an **employee**, no manager or admin can ever see my conversations, my usage patterns, my response times, or any score about me — because those surfaces and tables do not exist, not because a permission hides them. |
| PF-02-S3 | As a **company admin**, I still get the org-level adoption and health picture (aggregate-only, k ≥ 5) I need to justify the subscription — without acquiring a monitoring tool I'd have to take to the works council as such. |
| PF-02-S4 | As **Ethera (vendor)**, I keep the ops telemetry needed to run the platform (errors, latency, sync health, aggregate cost) while computing **no derived per-employee behavioral metrics or profiles**; raw content access exists only via the audited break-glass `support_grant` seam, disclosed in the checklist below. |
| PF-02-S5 | As a **security engineer**, the append-only `audit_log` keeps doing its forensic/compliance job, purpose-bound and unreachable from any analytics path — security logging is not repurposed into performance monitoring. |

### The data contract (referenced by S1–S5 and the checklist)

**NEVER collected / never computed (guaranteed by schema absence + denylist test, flag-independent):**
keystrokes, screen or app activity, idle/presence time, message read/response speed,
per-employee sentiment or emotion scores, productivity/performance/engagement scores,
per-employee rankings or leaderboards, cross-employee behavioral comparisons. **No
login-frequency or working-hours analytics are computed** — raw auth events (actor,
timestamp, ip) do exist in the registry-listed security log (`audit_log`, stated
retention), but no product surface computes metrics over them; release of that raw
trail is gated, audited, and policy-bound (AC12).

**Collected aggregate-only (Tier-3 style; customer-facing; k ≥ 5 suppression):**
org-level query volume, org-level feature-adoption counts, org-level connector/sync health,
aggregate latency and (future) aggregate cost. No individual attribution, ever.

**Collected per-user but NOT a monitoring surface (registry-listed, purpose-bound, access-restricted):**
Registry inclusion rule (precise, test-enforced): **any table carrying a person/user FK or
user-attributable key** MUST be registered — no exceptions. Registered entries:
`audit_log` (append-only security/compliance trail — actor, action, timestamp, ip; reachable
only via PC-06 compliance export, each export call itself audited with recorded purpose and
bound to the written release policy (AC12); itself flagged to lawyers as
co-determination-relevant), `user`/`refresh_token` (authentication), connector
credentials/cursors (sync correctness), the **ingestion and entity-spine tables** —
`email_message` (`from_person_id` + timestamps), `email_recipient` (person↔message links,
to/cc/bcc), `person`/`person_email`/`person_alias` — registered with purpose *"work-product
content for retrieval; access governed by PF-01 ACLs; no metrics computed over it"* and the
honest note that **content corpora are co-determination-relevant by nature** (see the
hard-truth box above), and — when lifted from v2 — pseudonymous `cost_events`
(billing/abuse). Each registry entry records: table, key, purpose, who can read it, and why
it is not a behavioral surface.

## 4. Acceptance criteria → tests (traceability matrix)

> ⭐ = compliance/security-critical. INV = `backend/tests/invariants/test_monitoring_surface_invariants.py`;
> BE = `backend/tests/identity/routes/test_org_settings_routes.py`. All ⬜ (spec-stage; names are the build contract).

| AC | Criterion | Proven by |
|---|---|---|
| ⬜ ⭐ PF-02-AC1 | **Flag exists, tenant-scoped, default ON:** `org_settings.non_monitoring_mode` defaults TRUE for every new org; table carries `org_id` NOT NULL + RLS policy + FORCE RLS and appears in the dynamic RLS enumeration. | BE `::test_new_org_defaults_non_monitoring_on`; `test_rls_invariants.py` (dynamic pickup of `org_settings`) |
| ⬜ ⭐ PF-02-AC2 | **Disabling is guarded + audited:** turn-off requires company-admin + typed confirmation + reason → `audit_log` event; wrong/absent confirmation → 400, nothing changed; re-enable also audited. | BE `::test_disable_requires_confirmation_reason_and_audits`, `::test_reenable_is_audited` |
| ⬜ ⭐ PF-02-AC3 | **Structural absence of per-user analytics routes (flag-independent):** no company-scoped route emitting per-user usage, cost, activity, or interaction data exists — such endpoints are never built, not built-and-403'd; the route-enumeration test is a **denylist** that FAILs on any match regardless of flag state. `require_non_monitoring_guard` is reserved for the genuinely unavoidable per-user surfaces (user management, auth administration) only. | INV `::test_no_company_route_emits_per_user_usage_data` (route-table denylist, flag-independent); BE `::test_guard_present_only_on_unavoidable_per_user_surfaces` |
| ⬜ ⭐ PF-02-AC4 | **Monitoring-surface audit (standing test, schema):** dynamic enumeration of all tables/columns; (a) any name matching the behavioral denylist (`productivity|performance_score|activity_score|response_time|idle|presence|keystroke|engagement|ranking|leaderboard|sentiment|working_hours`) → FAIL; (b) any table keyed per user storing counters/scores/timestamps-of-behavior that is **not** in `monitoring_registry.py` → FAIL, never SKIP. | INV `::test_no_behavioral_denylist_matches_in_schema`, `::test_every_per_user_table_is_registered_with_purpose` |
| ⬜ ⭐ PF-02-AC5 | **Monitoring-surface audit (standing test, code):** repo-wide grep gate — no source path outside the registry computes per-user aggregates over content/usage tables; CI fails on new hits (allowlisted-by-line, same style as the RLS engine-usage checks). | INV `::test_static_grep_no_unregistered_per_user_aggregation` |
| ⬜ PF-02-AC6 | **Aggregate-only with k-anonymity:** customer-facing aggregate endpoints suppress any group with fewer than 5 users (omitted, not shown as `n<5`). | BE `::test_aggregates_suppress_groups_below_k5` |
| ⬜ ⭐ PF-02-AC7 | **Cross-employee behavioral promotion ban (forward contract, unconditional):** the Learn-layer promotion schema MUST carry `category`; `category='behavioral'` is blocked at the gate **regardless of `org_settings.non_monitoring_mode`** — same flag-independence as the schema denylist — and the suppression is logged. The flag governs only employer-facing aggregate granularity, never this gate. Until Step 7 lands, the invariant test asserts the gate module exists-or-is-absent-together with the pipeline (no pipeline without gate). | INV `::test_learn_pipeline_absent_or_unconditionally_gated` (today: pipeline absent → pass; FIX_BEFORE_PROD entry keeps it honest at Step 7) |
| ⬜ ⭐ PF-02-AC8 | **Audit-log purpose limitation:** `audit_log` is readable only via PC-06 compliance export + platform console; no company-scoped analytics route selects from it. | INV `::test_audit_log_not_referenced_by_company_analytics_routes` |
| ⬜ PF-02-AC9 | **Erasure + RLS wiring (standing invariants):** `org_settings` is wired into the PC-06 erasure path with an explicit scrub-vs-retain decision (retain: it holds no personal data — org-level booleans + audited reasons live in `audit_log`), and carries a cross-tenant negative test. | erasure test `::test_erase_handles_org_settings`; `test_rls_invariants.py` |
| ⬜ PF-02-AC10 | **Ops telemetry preserved:** with the flag ON, health/sync/error/latency endpoints and (future) aggregate cost rollups still function — proving non-monitoring ≠ flying blind. | BE `::test_ops_telemetry_unaffected_by_flag` |
| ⬜ PF-02-AC11 | **Betriebsrat checklist artifact:** company-admin endpoint returns the itemized checklist (below) + flag state + registry dump + the audit-trail release policy (AC12) + latest invariant-test status, headed with the not-legal-advice disclaimer. | BE `::test_betriebsrat_checklist_contents_and_disclaimer` |
| ⬜ ⭐ PF-02-AC12 | **Compliance-export access is itself audited + policy-bound:** every call to the PC-06 compliance export (the only path to raw `audit_log` data) writes its own `audit_log` event with a recorded purpose; release is governed by a written policy — **DSAR, regulator request, or employee offboarding only** — embedded verbatim in the checklist artifact. | BE `::test_compliance_export_writes_audit_event_with_purpose`, `::test_checklist_embeds_release_policy` |

### The Betriebsrat checklist (the itemized §87(1) Nr. 6 mapping — content of AC11's artifact)

> **⚠️ Engineering input for the customer's legal counsel and works council. NOT legal
> advice. Non-Monitoring Mode does not exempt deployment from co-determination under
> BetrVG §87(1) Nr. 6 — it is designed to make the Betriebsvereinbarung short and
> verifiable.**

| # | Works-council concern (typical §87(1) Nr. 6 reading) | System property | Verified by |
|---|---|---|---|
| 1 | Does the system measure individual performance (Leistungskontrolle)? | No per-user productivity/performance metrics are computed or stored — schema denylist enforced. | AC4 standing test, re-run every CI |
| 2 | Does it record individual behavior (Verhaltenskontrolle)? | No behavioral profiles outside the employee's own private Tier-1 context (hard boundary; no company-admin product surface reads conversations). Disclosed up front: an audited break-glass `support_grant` seam allows exceptional vendor content access for support — grant-gated, time-boxed, fully audited, guard conditions listed in the registry. | AC4 + three-tier privacy model + registry |
| 3 | Can a manager see who uses the AI, how often, how fast? | No product surface computes or displays this — per-user analytics routes are not built (denylist-enumerated, flag-independent); the employer sees aggregates only, k ≥ 5. A raw append-only security trail exists (disclosed in the registry); its release is gated, audited, and policy-bound (DSAR / regulator / offboarding only). | AC3, AC6, AC12 |
| 4 | Can behavioral observations about one employee surface to others? | No. Learn-layer promotion of `behavioral`-category facts is blocked and logged **unconditionally — the block does not depend on any configuration flag**. | AC7 (gate contract, flag-independent) |
| 5 | Is the security log a hidden performance monitor? | No product surface computes or displays performance metrics from it. A raw append-only security trail exists (disclosed in the registry as co-determination-relevant, with stated retention), unreachable from any analytics path; its release is gated, audited, and bound to the written release policy (DSAR / regulator / offboarding only). | AC8, AC12 |
| 6 | What per-person data exists at all? | The complete registry (inclusion rule: any table with a person/user FK or user-attributable key; table, purpose, access scope) is included in this artifact — auth/security tables **and the ingested content corpora themselves** (email messages, recipients, person entities), honestly listed as co-determination-relevant work-product content over which no metrics are computed. The break-glass `support_grant` seam is disclosed here with its guard conditions. | AC4(b) registry |
| 7 | Can the employer quietly switch monitoring on? | No — there is nothing to switch on. Turning the flag OFF changes exactly one thing: aggregate small-group suppression (k ≥ 5) relaxes. **No employer-facing surface becomes per-user in either flag state**, and the behavioral-promotion gate is unaffected. Disabling still requires typed confirmation + reason and writes an immutable audit event the works council can demand. | AC1, AC2, AC3 |
| 8 | Can we re-verify after updates? | Yes — the standing invariant suite fails the build on any new monitoring surface; this artifact embeds the latest run status. | AC4, AC5, AC11 |

## 5. Implementation map

| Area | Files |
|---|---|
| Model + migration | `backend/app/identity/models/org_settings.py`; `backend/app/db/migrations/0013_org_settings.py` (TenantMixin, RLS policy, FORCE RLS, default TRUE) |
| Service + routes | `backend/app/identity/services/org_settings_service.py`; `backend/app/identity/routes/org_settings_routes.py`; `backend/app/identity/schemas/org_settings_schemas.py` |
| Enforcement seam | `backend/app/identity/dependencies.py` (`require_non_monitoring_guard` — single choke point, mirrors `get_tenant_session` discipline) |
| Registry | `backend/app/common/monitoring_registry.py` (allowlist of per-user tables: name, key, purpose, legal-basis hint, access scope) |
| Standing tests | `backend/tests/invariants/test_monitoring_surface_invariants.py`; additions to `test_rls_invariants.py` pickup; erasure test extension |
| Artifact | checklist renderer in `org_settings_service.py` → `GET /company/compliance/betriebsrat-checklist`; static copy exported to `docs/compliance/betriebsrat-checklist.md` |
| Audit | `audit_service` events: `org_settings.non_monitoring_mode_disabled` / `…_enabled` |

## 6. Decisions settled in this spec (pre-PR)

- **Default ON, friction to disable** — the DACH wedge is the default posture, not an
  upsell toggle; disabling is the exceptional, audited act (mirror of PC-06's
  destructive-action guards).
- **Guarantee by absence, not by permission** — wherever possible the contract is "the
  table/route does not exist" (testable by enumeration), falling back to a single guard
  dependency only where per-user data is operationally unavoidable (auth, audit, billing).
- **Registry over review** — every per-user-keyed table needs a registered purpose or the
  build fails. This converts "did anyone add a monitoring surface?" from a human review
  question into a standing invariant, the same move as `test_rls_invariants.py`.
- **Honesty about co-determination** — we never market "no works-council needed". The BAG's
  objective-suitability standard makes that claim false for any system touching employee
  email. The product claim is *verifiable minimal surface*, and every artifact carries the
  not-legal-advice disclaimer.
- **Tier-1 personal adaptation is untouched** — the employee's own AI learning about the
  employee is a private feature with a hard admin boundary; Non-Monitoring Mode governs
  *employer-facing* surfaces only. Conflating the two would gut the retention moat for no
  compliance gain.
- **Learn-layer contract fixed early — and unconditional** — AC7 encodes the
  promotion-gate requirement now, **flag-independent** (the behavioral ban is never
  toggleable; the flag governs only aggregate granularity), so Step 7 cannot ship a
  Nightly Board that leaks behavioral facts; the FIX_BEFORE_PROD entry makes skipping it
  impossible to do silently.
- **`org_settings` retained on erasure** — it holds org-level configuration, no personal
  data; the audited disable-reasons live in `audit_log`, which PC-06 already retains under
  Art. 17(3).

## 7. Remaining + notes

- **FIX_BEFORE_PROD (new entries):** (1) monitoring-surface registry re-audit before every
  prod release until the invariant suite is proven in live CI; (2) Learn-layer behavioral
  gate (AC7) must be implemented and live-verified before Step 7 ships — a green stub is
  not closure.
- **Cost-tracking lift (from `one-ai - v2`):** pseudonymous user reference + registry entry
  + aggregate-only customer exposure are preconditions for lifting `cost_events` into this
  backend.
- **Sibling epic:** PF-01 (permission-faithful retrieval — per-mailbox/per-source ACLs
  below the org_id RLS seam) is the companion guarantee; this epic deliberately does not
  depend on it.
- **Source-strategy caveat:** the pivot document (`One AI Defensibility & Bulletproofing
  Strategy_v1.0.md`) was not locatable at spec time; this epic is grounded in the current
  codebase, the Project Bible privacy model, and the ICP/GTM works-council research. If the
  pivot doc resurfaces with conflicting decisions, reconcile before the PR.
- **Dynamic adversarial QA pass** (house discipline): at PR time, an adversarial live pass —
  attempt to reconstruct per-user behavioral metrics from every company-scoped endpoint and
  from raw DB access as `oneai_app`; any reconstruction path → blocking finding.
- **Lawyer hand-off:** the checklist artifact is an input to the customer's
  Betriebsvereinbarung negotiation; Ethera provides engineering evidence, never legal
  conclusions.
