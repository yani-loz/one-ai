# TC-SG-010: Invariant test runs WITH TEETH on the migrated dev DB (not silently skipped)

| Field | Value |
|---|---|
| **ID** | TC-SG-010 · **Suite** B · **Type** Positive · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** the DB-level invariant test silently `pytest.skip`s on the live dev DB (sentinel policy absent /
non-migrated), so it has zero teeth in practice and only LOOKS green.

**Command**
```
docker compose exec -T backend python -m pytest tests/identity/models/test_rls_invariants.py --no-cov -rs -v
```
**Evidence**
```
collected 2 items
test_tenant_model_enumeration_is_non_vacuous_and_content_blind PASSED [ 50%]
test_every_tenant_table_has_rls_enabled_and_isolation_policy   PASSED [100%]
============================== 2 passed in 0.46s ===============================
(-rs short summary shows NO 'SKIPPED' lines — 0 skipped; both tests genuinely executed against the migrated dev DB.)
```
**Verdict:** Defense holds. Both tests PASS and neither SKIPS on the live migrated dev container — the DB-level invariant
actually executes its assertions where it ships. `-rs`/`-v` confirm 0 skipped, removing the silent-skip ambiguity of a
bare "2 passed".
