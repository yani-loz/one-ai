# TC-SG-013: FAITHFUL teeth proof — forgotten org_isolation policy on support_grant turns the REAL test RED

| Field | Value |
|---|---|
| **ID** | TC-SG-013 · **Suite** B · **Type** Negative · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** the invariant test is decorative — even with a tenant table's `org_isolation` policy genuinely missing
on a real migrated schema, the REAL test still passes (or skips), so it would NOT catch a future migration that forgets a
policy. (A synthetic `audit_log` demo is rejected as proof — `audit_log` is not a `TenantMixin` subclass and never enters
the test's iteration set; only a real forgotten policy on an enumerated table proves teeth.)

**Command** — one Bash invocation, throwaway scratch DB with `trap`-EXIT drop
```
SDB=oneai_sg_teeth_<ts>; trap 'psql -U oneai -d oneai -c "DROP DATABASE IF EXISTS $SDB"' EXIT
psql -U oneai -d oneai -c "CREATE DATABASE $SDB"
docker compose exec -T -e POSTGRES_DB=$SDB backend alembic upgrade head            # real schema 0001→0006
psql -U oneai -d $SDB -c "DROP POLICY org_isolation ON support_grant"              # non-sentinel table
docker compose exec -T -e POSTGRES_DB=$SDB backend python -m pytest tests/identity/models/test_rls_invariants.py --no-cov -rs -v
```
**Evidence**
```
[baseline, migrated, all policies present] → 2 passed in 0.34s
[after DROP POLICY org_isolation ON support_grant; relrowsecurity still 't', sg_policies=0]:
  test_every_tenant_table_has_rls_enabled_and_isolation_policy FAILED [100%]
>   assert await _policy_exists(session, table), (...)
E   AssertionError: support_grant: missing the 'org_isolation' RLS policy
E   assert False
tests/identity/models/test_rls_invariants.py:127: AssertionError
========================= 1 failed, 1 passed in 0.97s =========================
Cleanup: trap dropped the scratch DB; real `oneai` still shows org_isolation on BOTH users + support_grant (untouched).
```
**Verdict:** Teeth PROVEN, faithfully. Dropped the policy on the NON-SENTINEL table `support_grant` (RLS still ENABLED —
only the policy removed), so the `users` sentinel guard is not tripped: the test proceeds past the skip, iterates sorted
`['support_grant','users']`, and FAILS on the first table with the exact predicted `AssertionError`. This is the real
forgot-the-policy regression the test defends against. Provisioned via real alembic, not a synthetic table. Independently
reproduced by the verify agent (scratch DB `oneai_sg_1780389144`). *(Dropping the SENTINEL `users` policy instead only
trips the SKIP — see TC-SG-015.)*
