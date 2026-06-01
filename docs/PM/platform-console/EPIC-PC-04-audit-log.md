# EPIC PC-04 — Append-only audit_log + admin/AI action trail

| Field | Value |
|---|---|
| **Epic ID** | PC-04 |
| **Module** | Platform Console (`PC`) |
| **Status** | 🟢 Backend done (PR-4) — **user.\* emission + the frontend audit-trail viewer (AC8) remain** (the PC-04a/04b split, mirroring 3a/3b) |
| **Branch** | `feat/platform-lifecycle` (continues the stack) |
| **PR** | PR-4 (backend core: table + trigger, auth & org-lifecycle emission, read API) |
| **Review** | [docs/audits/2026-06-01_platform-audit-pr4-review.md](../../audits/2026-06-01_platform-audit-pr4-review.md) — 5 confirmed (1 high), all fixed; 1 dismissed |
| **Depends on** | PC-01..PC-03a (the actions it records); the Identity auth domain |
| **Enables** | **PC-05** break-glass (every grant + access writes here) and **PC-06** erasure (deletion certificates reference it) |
| **Closes (FIX_BEFORE_PROD)** | "Add the append-only `audit_log` table" + "Log all auth and permission-change events" |
| **Refs** | `.claude/rules/security.md` (audit trail: who/what/when/where/why, append-only); `docs/Project_Bible.md` §189 (immutable audit trail) |

## 1. Goal & context

Give One AI an **immutable, append-only audit trail** — the compliance spine DACH
procurement gates on ("show me exactly what an incident investigation would look like; what
logs exist, who did what, when"). Every consequential action — auth events, user/role
changes, org onboarding, and the **PC-03a lifecycle actions** (suspend/reactivate, legal
hold) — is recorded with **who / what / when / where (entity + IP) / why**. The log is
**metadata about actions, never tenant content** — recording "admin X suspended org Y", not
the org's data. This is also the substrate the break-glass flow (PC-05) and deletion
certificates (PC-06) attach to.

> "admin/AI action trail": this PR covers **admin + auth + lifecycle** actions. **AI action**
> logging (which agent did what, what it retrieved, what tools it ran) is a forward hook —
> the schema accommodates it (`actor_type='system'`/`'agent'`, `action` namespace), but no AI
> actions exist to log until Connect/Ask/Learn land.

## 2. Scope

**In scope**
- An `audit_log` table, **append-only enforced at the DB** (a `BEFORE UPDATE/DELETE` trigger
  that raises — robust even though the app currently connects as a superuser role, unlike
  grant-revocation which waits on the least-privilege-role work, same as RLS).
- An append-only repository + a small audit-writer service; **request context** (IP,
  request id) captured via middleware / context-var so services can emit events without
  threading the `Request` everywhere.
- **Event emission** wired into: auth (`auth.login.success`, `auth.login.failure`,
  `auth.refresh`, `auth.logout`), user management (`user.create`, `user.deactivate`,
  `user.role_change`), onboarding (`org.onboard`), and PC-03a lifecycle (`org.suspend`,
  `org.reactivate`, `org.legal_hold.set/clear`).
- Read endpoints: `GET /platform/orgs/{id}/audit` (one tenant's trail) and
  `GET /platform/audit` (global, filterable), platform-gated + paginated.
- A frontend **audit-trail viewer** on the org detail screen (a section/tab), newest-first.

**Out of scope (later)**
- AI/agent action logging (forward hook only — see above).
- SIEM export / log shipping; long-term retention tiering.
- Break-glass grant lifecycle (PC-05) — it will *write* to this log.

## 3. User stories

| ID | Story |
|---|---|
| PC-04-S1 | As a platform admin, I can see the **trail of actions** taken on a company (who suspended it, when, who placed a legal hold). |
| PC-04-S2 | As a compliance officer, the audit log is **tamper-evident / immutable** — no one can edit or delete entries. |
| PC-04-S3 | As an incident responder, every **auth event** (incl. failed logins) and **admin action** is recorded with actor + IP + timestamp. |
| PC-04-S4 | As the system, the audit log records **actions, not tenant content** — it never weakens content-blindness. |

## 4. Acceptance criteria → tests (traceability matrix)

> ⭐ = security/compliance-critical. BE = `backend/tests/identity/`. ✅ proven · ⏳ remaining slice.

| AC | Criterion | Proven by |
|---|---|---|
| ✅ ⭐ PC-04-AC1 | The `audit_log` table is **append-only**: any `UPDATE` or `DELETE` raises (DB trigger), even for the superuser app role; INSERT succeeds. | BE `models/test_audit_log.py::test_audit_log_update_is_blocked_by_trigger`, `::test_audit_log_delete_is_blocked_and_row_survives`, `::test_audit_log_insert_succeeds` (+ live psql proof) |
| ✅ PC-04-AC2 | Auth events are recorded: `login.success`, `login.failure` (actor_email = attempted email, actor_id null), `refresh`, `logout`. | BE `routes/test_platform_audit_routes.py::test_login_success_records_event_with_denormalized_email`, `::test_failed_login_records_failure_without_secrets` |
| 🟡 PC-04-AC3 | Lifecycle + management events recorded: `org.suspend`/`reactivate`, `org.legal_hold.set`/`clear`, `org.onboard` ✅; `user.create`/`deactivate`/`role_change` ⏳. | BE `::test_suspend_records_event_metadata_only`, `::test_org_audit_is_newest_first_and_paginated` (reactivate + legal_hold.set); `test_platform_auth_service.py` onboard tests emit `org.onboard`. **user.\* → PC-04a remaining.** |
| ✅ ⭐ PC-04-AC4 | **No secrets**: no audit row ever contains a password, hash, or token (asserted on `details`/serialized rows). | BE `::test_failed_login_records_failure_without_secrets` (attempted password + `_PASSWORD` absent from the serialized trail) |
| ✅ PC-04-AC5 | `GET /platform/orgs/{id}/audit` returns that org's trail newest-first, paginated, **metadata only**. | BE `::test_org_audit_is_newest_first_and_paginated` |
| ✅ ⭐ PC-04-AC6 | Both audit endpoints reject a **company token** (exactly 401) and require the platform gate. | BE `::test_audit_endpoints_reject_company_token` |
| ✅ PC-04-AC7 | The **actor_email is denormalized** (durable attribution); the row has **no FK** to users/orgs, so it survives their deletion. | BE `::test_login_success_records_event_with_denormalized_email` (actor_email persisted); model has no FK (PC-06 erasure-safe) |
| ⏳ PC-04-AC8 | Frontend: the detail screen shows the org's audit trail; a UI action (e.g. suspend) **appears in the trail** on reload. | **PC-04a (frontend viewer) — not yet built.** |

## 5. Design sketch (to validate before the migration)

**Table `audit_log`** (not tenant-scoped — platform/compliance data spanning all orgs; each
row *tags* the affected `org_id`):

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | `gen_random_uuid()` |
| occurred_at | timestamptz | `now()` default |
| actor_type | varchar(20) | CHECK `platform_admin` \| `user` \| `system` (room for `agent`) |
| actor_id | uuid null | null for failed logins / system |
| actor_email | varchar(320) null | denormalized for durable attribution (AC7) |
| action | varchar(64) | dotted namespace, e.g. `org.suspend` |
| org_id | uuid null | the affected tenant (for per-org filtering) |
| entity_type | varchar(40) null | e.g. `organization`, `user` |
| entity_id | uuid null | |
| details | jsonb | structured, **never secrets** |
| ip_address | inet null | from request context |
| request_id | varchar(64) null | correlation |

- **Immutability**: `CREATE TRIGGER audit_log_no_mutation BEFORE UPDATE OR DELETE … RAISE`.
  (Grant-revocation is the complementary control once the app runs as a non-superuser role —
  tracked with the RLS-enforcement item.)
- **Indexes**: `(org_id, occurred_at desc)`, `(action, occurred_at desc)`.
- **Writer**: `AuditService.record(actor, action, *, org_id=None, entity=None, details={}, ip=None)`;
  request IP/request-id captured by middleware into a context-var. Emission must **never block
  or fail the primary action** loudly — decide commit-coupling (same tx vs best-effort) during
  build; for security events, prefer same-transaction so the action and its record commit together.

## 6. Decisions settled during the PR

- **Transaction coupling (the core decision) — SETTLED: same-tx for success, independent
  for failure.** Success events (`login.success`, `refresh`, `logout`, `org.*`, `onboard`)
  are appended on the **request session**, so they commit atomically with the action — a
  successful action can **never** be silently unlogged. The inverse risk (a bad audit row
  failing the action) is bounded by building every row field from already-validated inputs,
  so a valid action cannot produce an invalid row. Failed/blocked logins raise (the request
  rolls back), so they use an **independent** committed session — the record survives, and a
  best-effort try/except keeps an audit hiccup from turning a clean 401/403 into a 500. The
  same-tx-success tradeoff + the outbox alternative are tracked in `FIX_BEFORE_PROD.md`.
- **Failed-login logging stores the *attempted* email** — confirmed it never surfaces to the
  unauthenticated response (the login reply stays a generic 401; the trail is platform-gated),
  so it adds **no enumeration oracle**. The suspended-login `auth.login.blocked` event is also
  recorded (valuable for incident response) on the same independent path.
- **Content-blindness** — every `details` payload is writer-controlled metadata
  (`{from_status,to_status}`, `{legal_hold}`, `{slug,name}`, `{reason}`); no tenant content,
  no secret. AC4 pins this with a serialized-trail assertion.
- **IP source = `request.client.host` only** (validated as a real IP, else null); X-Forwarded-For
  is deliberately untrusted. `ip_address` stored as `String(45)` (not `inet`) to avoid an
  asyncpg bind error rolling back a same-tx login — both tracked in `FIX_BEFORE_PROD.md`.

## 7. Remaining (the PC-04a slice) + notes

- **Frontend audit-trail viewer (AC8)** — a section/tab on the org detail screen, newest-first,
  reading `GET /platform/orgs/{id}/audit`. Deferred for context budget, mirroring the 3a/3b split.
- **`user.*` emission (AC3 tail)** — `user.create` / `deactivate` / `role_change` from
  `UserService` (tenant session; `audit_log` is intentionally outside RLS so a tenant-session
  INSERT works). Plus platform-admin login emission + platform-admin `actor_email` denormalization.
- **Dynamic QA (Target 06)** — an adversarial validation pass over the live audit pipeline
  (immutability under load, no-secret invariant, the independent-writer survival) to follow,
  per the established per-PR pattern.
