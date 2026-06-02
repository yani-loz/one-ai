# TC-SG-011: Live-catalog truth — relrowsecurity=t, relforcerowsecurity=f, org_isolation qual correct on both tables

| Field | Value |
|---|---|
| **ID** | TC-SG-011 · **Suite** B · **Type** Positive · **Severity if fail** Medium |
| **Result** | ⚠️ Pass-with-concern · **Tag** 📋 CONFIRMS-DOCUMENTED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** the live catalog disagrees with the test's premise — RLS not actually enabled, `org_isolation`
missing, or keyed on the wrong GUC so the future enforcement step is built on sand.

**Command**
```
docker compose exec -T db psql -U oneai -d oneai -c "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname IN ('users','support_grant') AND relnamespace='public'::regnamespace ORDER BY relname;"
docker compose exec -T db psql -U oneai -d oneai -c "SELECT tablename, policyname, qual, with_check FROM pg_policies WHERE tablename IN ('users','support_grant') ORDER BY tablename;"
```
**Evidence**
```
   relname     | relrowsecurity | relforcerowsecurity
---------------+----------------+---------------------
 support_grant | t              | f
 users         | t              | f

  tablename    |  policyname   |                          qual                                | with_check
---------------+---------------+--------------------------------------------------------------+------------
 support_grant | org_isolation | (org_id = (current_setting('app.current_org_id', true))::uuid)| (same)
 users         | org_isolation | (org_id = (current_setting('app.current_org_id', true))::uuid)| (same)
```
**Verdict:** Catalog matches ground truth. RLS ENABLED on both; `org_isolation` present on both with USING and WITH CHECK
keyed on `current_setting('app.current_org_id', true)::uuid`. **CONCERN (expected/documented, NOT a defect):**
`relforcerowsecurity=f` — FORCE is intentionally deferred to migration `0007` (migration 0003 docstring +
`rls-jwt-enforcement-plan.md` + `FIX_BEFORE_PROD.md`), and the app connects as superuser/owner `oneai`
(`rolsuper=rolbypassrls=t`), so DB-level enforcement is INERT today (see TC-SG-020). Recorded as the expected pre-0007 state.
