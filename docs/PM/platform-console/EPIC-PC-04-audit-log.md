# EPIC PC-04 — Append-only audit_log + admin/AI action trail

| Field | Value |
|---|---|
| **Epic ID** | PC-04 |
| **Module** | Platform Console (`PC`) |
| **Status** | ⏳ Planned (spec — not yet built) |
| **PR** | PR-4 |
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

## 4. Acceptance criteria (testable — tests to be written with the PR)

> ⭐ = security/compliance-critical. Each criterion will link to its real `test_name` once built.

| AC | Criterion |
|---|---|
| ⭐ PC-04-AC1 | The `audit_log` table is **append-only**: any `UPDATE` or `DELETE` raises (DB trigger), even for the app role. (Test: attempt update/delete → error; insert succeeds.) |
| PC-04-AC2 | Auth events are recorded: `login.success`, `login.failure` (actor_email = attempted email, actor_id null), `refresh`, `logout` — each with action, actor, IP, timestamp. |
| PC-04-AC3 | Lifecycle + management events are recorded: `org.suspend`/`reactivate`, `org.legal_hold.set`/`clear`, `org.onboard`, `user.create`/`deactivate`/`role_change`. |
| ⭐ PC-04-AC4 | **No secrets**: no audit row ever contains a password, hash, or token (asserted on the written `details`/columns). |
| PC-04-AC5 | `GET /platform/orgs/{id}/audit` returns that org's trail newest-first, paginated, **metadata only**. |
| ⭐ PC-04-AC6 | Both audit endpoints reject a **company token** (exactly 401 — discriminating, real-admin sub) and require the platform gate. |
| PC-04-AC7 | The **actor_email is denormalized** so a deleted/renamed user's past actions remain attributable (the row survives FK changes). |
| PC-04-AC8 | Frontend: the detail screen shows the org's audit trail; an action taken in the UI (e.g. suspend) **appears in the trail** on reload. |

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

## 6. Risks / decisions to settle during the PR

- **Transaction coupling** of the audit write vs the action (same-tx integrity vs availability).
- **Failed-login logging** stores the *attempted* email — acceptable for an internal,
  append-only security log, but confirm it never surfaces to an unauthenticated response (no
  new enumeration oracle).
- **Volume**: auth events are high-frequency; confirm indexing + a retention plan before prod.
- **Content-blindness**: review every `details` payload so no tenant content leaks into an
  audit row (the rule that bounds this whole module).

## 7. Notes

- Per the PM convention, the **traceability matrix (AC → real `test_name`)** and the manual QA
  plan are filled in when PR-4 is built; this doc is the up-front spec.
