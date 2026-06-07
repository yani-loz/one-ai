# RLS enforcement + JWT_SECRET fail-closed — validated implementation plan

> Output of the `rls-jwt-enforcement-design` workflow (map → 3 competing designs → synthesis →
> 3-lens adversarial stress). **Verdict: the design is sound (no open/silent leak; every failure
> mode is closed/loud), but 4 fixes must be folded into the flip commit before it goes green.**
> This doc is the durable plan; implement it as a focused, branch-isolated change — it is the
> single most invasive change in the repo (DB roles + connection layer + CI + docker).

> **STATUS — IMPLEMENTED + LIVE-VERIFIED (2026-06-07, pending commit/PR).** Landed as migration
> `0009_enforce_rls` + `scripts/provision_roles.py` + the `database.py` three-engine split + the
> `SessionLocal`→`GlobalSessionLocal` rename across all importers + docker/CI provisioning steps +
> the `test_rls_invariants.py` FORCE assertion & live two-role isolation proof (with teeth) +
> cross-org-write rejection. Verified on a throwaway DB: `oneai_app` isolates to one org,
> `oneai_global` bypasses, a cross-org write is rejected with a row-level-security error, and the
> full backend suite is green (**374 passed**, ruff clean). Residuals tracked in `FIX_BEFORE_PROD.md`.

## Two reframes the map surfaced
1. **JWT_SECRET fail-closed is ALREADY built + tested.** `Settings._forbid_insecure_defaults_in_production`
   (`config.py`) raises a hard boot failure when `jwt_secret`/`postgres_password` are the dev default.
   The task framing was stale. **The only real gap:** the gate fires on `app_env == "production"` *exact
   match*, so **staging / a typo'd `app_env` boots with the public dev secret**. Fix = generalize to
   "fail unless `app_env in {local,test}`" + add the 2 new role passwords to the guarded list.
2. **RLS enforcement is a role/connection change, NOT a schema change.** The `org_isolation` policies on
   `users` (0003) + `support_grant` (0006) are already correct + fail-closed (NULL GUC → zero rows). They're
   inert only because the app connects as `oneai` (SUPERUSER + OWNER, both bypass RLS).

## Chosen design — three roles, two engines (maps 1:1 onto the existing session seam)
| Role | Attributes | Used by | Why |
|---|---|---|---|
| `oneai` | SUPERUSER + OWNER (unchanged) | **Alembic/DDL only** (`env.py`), never serves HTTP | migrations need DDL; keeping it owner avoids the FORCE-locks-out-owner trap |
| `oneai_app` | NOSUPERUSER, non-owner, **no BYPASSRLS**, DML-only | a new **tenant** engine → `scoped_session` → `get_tenant_session` | non-owner/non-super → the ENABLE'd policies finally apply; **this is the role RLS enforces against** |
| `oneai_global` | NOSUPERUSER, non-owner, **BYPASSRLS**, DML-only, no DDL | a new **global** engine → `get_session` | the legitimately cross-org / pre-org flows |

**The split is a connection change, near-zero app refactor:** `database.py`'s one `SessionLocal` becomes
`TenantSessionLocal` (feeds `scoped_session`; keeps the `after_begin` GUC listener) + `GlobalSessionLocal`
(feeds `get_session`; **never** gets the GUC listener). The privilege boundary becomes a *static pool
property* — the only failure mode is a code-review-visible mis-wire in one file.

**Why BYPASSRLS-as-a-role, not `SET ROLE` / a bypass-GUC / `SECURITY DEFINER`:** login resolves a user
*before* any org is known; platform/erasure/audit span *all* orgs — a single-valued `app.current_org_id`
GUC can't express "all orgs"/"no org yet". And `SET ROLE`/`SET app.bypass_rls` are connection state that
**survives asyncpg checkin and contaminates the next request** (the exact hazard `is_local=true` avoids).
BYPASSRLS as a static role attribute on its own pool is leak-free by construction.

## Routing (verified against `dependencies.py`)
- **GLOBAL engine (`get_session`, 6 providers):** `get_auth_service` (login/refresh/me), `get_platform_auth_service`
  (cross-org onboard INSERT), `get_platform_org_service` (lifecycle + the `user_count` JOIN), `get_audit_service`,
  `get_platform_support_service`, `get_erasure_service`. **+ `audit_service.py`'s independent failed-login writer
  (`SessionLocal`) → GlobalSessionLocal** (pre-auth, cross-org). **+ the seed script's org/admin inserts.**
- **TENANT engine (`get_tenant_session`, RLS-enforced, MUST NOT get bypass):** `get_user_service`,
  `get_company_support_service`. These are the flows RLS exists to protect.

> **Failure-direction asymmetry (the safety argument):** a global flow wrongly on the tenant engine fails
> **closed/loud** (empty/500, a test catches it). A tenant flow wrongly on the global engine fails
> **open/silent** (cross-tenant leak). So the only catastrophic mis-wire is code-review-visible in one file.
> The silent breakers without bypass: **erasure deletes 0 rows yet certifies success (fake GDPR erasure)**,
> and the `user_count` JOINs collapse to 0. Both get explicit positive-data tests.

## The 4 fixes the stress test requires (fold into the flip commit)
1. **No `psql` in the backend image.** Provision role LOGIN+passwords via an **asyncpg** script
   (`scripts/provision_roles.py`, run after `alembic upgrade head`, before uvicorn/pytest) — asyncpg is
   already a dep. (Password is a safely-quoted literal, not a bind param — `ALTER ROLE` takes no `$1`.)
2. **CI has no provisioning step.** Add `uv run python -m scripts.provision_roles` to `.github/workflows/ci.yml`
   **between** `alembic upgrade head` and `pytest` — same commit as the flip, or CI goes red.
3. **The flip's file list is incomplete.** Add `audit_service.py` (prod code importing `SessionLocal`),
   `tests/identity/models/test_audit_log.py`, `tests/core/test_database.py`, and **both conftests** to the
   flip commit (they import the renamed symbols → collection breaks otherwise). Re-run the importer grep as a
   pre-flip gate.
4. **Single-source the role passwords.** The config default and the `ALTER ROLE` value must read the **same**
   env var (`ONEAI_APP_PASSWORD`/`ONEAI_GLOBAL_PASSWORD`) or they silently drift → asyncpg auth failure.

Medium/low (track, don't block local/CI): BYPASSRLS role creation needs a *true* superuser (not guaranteed on
RDS/Cloud SQL — fallback = a scoped permissive `app.bypass_rls` policy); rollback = set the 4 role user/pwd
vars back to the `oneai` owner creds (URLs are computed_fields); `DROP ROLE` fails with live connections.

## Migration (the RLS flip — DDL only, owner runs it; the NEXT migration after Connect's tables — do NOT pin the number)
`CREATE ROLE oneai_app/oneai_global` (NOLOGIN + correct attrs; LOGIN+password set out-of-band by the script,
so no secret in VCS) · DML `GRANT`s + `ALTER DEFAULT PRIVILEGES` · `ALTER TABLE … FORCE ROW LEVEL SECURITY`
on EVERY tenant table (belt-and-suspenders; not strictly required since the runtime roles aren't owners).
**No policy rewrite** — the per-table policies (0003/0006/0007/0008) are already correct. NB: every tenant
table that lands before the flip must be in the FORCE list; the standing-invariant test enumerates them
dynamically, so it will flag a forgotten policy (Connect's step-3 schema added 9 tables in 0008).

## The standing-invariant test — DONE (ENABLE + policy); FORCE assertion folds into the flip
✅ **Shipped** (`backend/tests/identity/models/test_rls_invariants.py`, commit `f8a4fbd`): a test that
**dynamically enumerates** every `TenantMixin` subclass (not a hardcoded `{users, support_grant}`) and asserts
each table has `relrowsecurity` (ENABLE) + an `org_isolation` policy, with two anti-rot guards (non-empty +
`{User, SupportGrant}` present; the 4 content-blind platform tables stay out). Skips loudly on a non-migrated DB.
**Deferred to THIS flip commit:** add the `relforcerowsecurity` assertion to the same test once the flip
migration sets FORCE — asserting it earlier would fail (FORCE isn't on yet). (Also extend the erasure-path completeness
invariant the same way.)

> **Also done as a safe pre-slice (commit `f8a4fbd`):** reframe-1's JWT gate generalization —
> `Settings.requires_secure_secrets` + `_forbid_insecure_defaults_outside_dev` now fail boot for any `app_env`
> outside `{local, test}` (staging / typo no longer slip through). Adding the 2 new role passwords
> (`ONEAI_APP_PASSWORD`/`ONEAI_GLOBAL_PASSWORD`) to the guarded `insecure` list still belongs to THIS flip
> commit (they don't exist until then).

## Verification (must prove BOTH, on a migrated + role-provisioned DB)
- **Isolation enforces:** as `oneai_app`, `SET app.current_org_id='<orgA>'; SELECT * FROM users` returns **zero**
  org-B rows; a cross-org write is rejected. As `oneai_global`, the same query spans orgs (bypass works).
- **No flow breaks:** login / refresh / `/auth/me` / onboarding / break-glass / **erasure actually deletes
  (`users_erased>0`, rows gone)** / audit reads all still work end-to-end.
- **JWT:** `app_env=staging` + dev secret → boot **fails** (new test); `local`/`test` still boot.

## Risk & rollback
The flip is **atomic** (migration + provisioning + engine split + all importers + conftest + CI land together).
Kill-switch: point both runtime engines back at the `oneai` owner creds (one config change) — RLS goes inert,
app recovers immediately. Keep the flip migration's downgrade `DROP ROLE` optional + drain connections first.
