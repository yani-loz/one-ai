# TC-SG-015: NEW — sentinel blind spot: a forgotten policy on the SENTINEL table `users` makes the test SKIP, not FAIL

| Field | Value |
|---|---|
| **ID** | TC-SG-015 · **Suite** B · **Type** Adversarial · **Severity if fail** Low |
| **Result** | ⚠️ Pass-with-concern · **Tag** 🆕 NEW (Low) · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** the same forgot-the-policy regression the test catches on `support_grant` (TC-SG-013) is ALSO caught
on `users`. Probe: drop `org_isolation` on the SENTINEL table of an otherwise fully-migrated DB and expect a RED.

**Command** — scratch DB, fully migrated, then drop the SENTINEL policy
```
psql -U oneai -d oneai -c "CREATE DATABASE $SDB"
docker compose exec -T -e POSTGRES_DB=$SDB backend alembic upgrade head
psql -U oneai -d $SDB -c "DROP POLICY org_isolation ON users"          # the sentinel table
docker compose exec -T -e POSTGRES_DB=$SDB backend python -m pytest tests/identity/models/test_rls_invariants.py::test_every_tenant_table_has_rls_enabled_and_isolation_policy --no-cov -rs -v
```
**Evidence**
```
[migrated head, then DROP POLICY org_isolation ON users (sentinel)]
  test_every_tenant_table_has_rls_enabled_and_isolation_policy SKIPPED [100%]
SKIPPED [1] ...:108: RLS policies are migration-only and absent on this database (fresh create_all DB)...
============================== 1 skipped in 0.37s ==============================
(DB is fully migrated; only the users policy was removed — yet the test reads it as "non-migrated" and SKIPS.)
```
**Verdict:** Not a fail (the test's contract is to skip on a non-migrated DB, and a correctly-migrated `users` always has
its policy), but a real, UNDOCUMENTED caveat. The sentinel (`_SENTINEL_TABLE='users'`, line 43/107) cannot distinguish
"DB not migrated" from "DB migrated but the users-policy was dropped/forgotten" — both make it SKIP. So the ONE table used
as the migration sentinel is the only tenant table whose forgotten policy escapes the teeth; every table enumerated after
the sentinel (`support_grant`) is fully protected (TC-SG-013). **NEW:** not in `test_config.py` (different module), not in
`FIX_BEFORE_PROD.md`. **Severity Low:** narrow trigger (the `users` policy specifically missing on a migrated DB) and
DB-level RLS is inert today anyway. Reproduced three times (Suite B + the independent verify agent's own scratch DB
`oneai_sg_sentinel_*`). **Remediation:** skip on a migration-independent signal (e.g. `alembic_version`) so the sentinel
table's own policy is asserted, not used as the skip oracle.
