# TC-SG-014: Skip-on-non-migrated correctness — LOUD skip on a pre-migration DB, no false pass

| Field | Value |
|---|---|
| **ID** | TC-SG-014 · **Suite** B · **Type** Negative · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** on a DB where the migration-only policies are absent, the DB-level test FALSE-PASSES (vacuously green)
instead of skipping — masking an un-migrated/unprotected database as compliant.

**Command** — same scratch lifecycle, BEFORE running alembic
```
psql -U oneai -d oneai -c "CREATE DATABASE $SDB"
docker compose exec -T -e POSTGRES_DB=$SDB backend python -m pytest tests/identity/models/test_rls_invariants.py --no-cov -rs -v
```
**Evidence**
```
[empty scratch DB, no migrations run → sentinel users policy absent]
  test_tenant_model_enumeration_is_non_vacuous_and_content_blind PASSED  [ 50%]
  test_every_tenant_table_has_rls_enabled_and_isolation_policy   SKIPPED [100%]
SKIPPED [1] ...:108: RLS policies are migration-only and absent on this database (fresh create_all DB). Run against a migrated DB (alembic upgrade head).
========================= 1 passed, 1 skipped in 0.40s =========================
```
**Verdict:** Skip branch is reachable and correct. On a non-migrated DB the sentinel (`org_isolation` on `users` absent)
trips `pytest.skip` with an explicit, loud reason rather than false-passing. The enumeration guard still PASSES (it needs no
DB), so it is never silently disabled by the skip. *(This very mechanism is what TC-SG-015 shows can misfire on a migrated
DB — same skip, wrong premise.)*
