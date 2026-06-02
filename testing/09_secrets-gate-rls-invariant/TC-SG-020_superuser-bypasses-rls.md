# TC-SG-020: Superuser app role bypasses the org_isolation RLS policy on users (DB enforcement inert)

| Field | Value |
|---|---|
| **ID** | TC-SG-020 · **Suite** C · **Type** Adversarial · **Severity if fail** High (if undocumented) |
| **Result** | ⚠️ Pass-with-concern · **Tag** 📋 CONFIRMS-DOCUMENTED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** if `org_isolation` were actually applied to the connecting role, pinning `app.current_org_id` to org A
and selecting org-B rows would return 0. If it returns >0, the policy is bypassed and DB-level isolation is inert.

**Command** — READ-ONLY, transaction ROLLBACK; org A/B discovered via `SELECT org_id, count(*) FROM users GROUP BY org_id`
```
docker compose exec -T db psql -U oneai -d oneai \
  -c "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;" \
  -c "BEGIN; SELECT set_config('app.current_org_id','1f9e5e89-...-c63d', true);
      SELECT count(*) FROM users WHERE org_id='bd94a689-...-e06d';
      SELECT count(*) FROM users; ROLLBACK;"
```
**Evidence**
```
current_user=oneai | rolsuper=t | rolbypassrls=t
relrowsecurity: users=t, support_grant=t  |  relforcerowsecurity: users=f, support_grant=f
GUC confirmed pinned: app.current_org_id = 1f9e5e89-...-c63d (org A = globex)
org_B_rows_visible_despite_guc_pinned_to_A = 2      ← org B's rows readable
all_users_visible = 4 (both orgs)   |   ROLLBACK — strictly read-only, no writes
```
**Verdict:** DB-level org isolation is INERT as documented. The `org_isolation` policy exists and the tables are ENABLEd,
but the app connects as `oneai` which is SUPERUSER + table OWNER (`rolsuper=rolbypassrls=t`) — both unconditionally bypass
RLS, and FORCE is not set. With the GUC pinned to org A, all of org B's rows remain readable, so the application-level
`WHERE org_id` is the ONLY active control — one missed scope leaks cross-tenant with no DB backstop. Documented in
`rls-jwt-enforcement-plan.md` (lines 16-17, 23) and `FIX_BEFORE_PROD.md:61`. Closes only with migration `0007`. **Not a
defect — the precise boundary of done.** Severity would be High if undocumented.
