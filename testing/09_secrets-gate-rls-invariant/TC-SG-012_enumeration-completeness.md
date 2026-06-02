# TC-SG-012: Enumeration completeness — every discovered TenantMixin subclass has live RLS + org_isolation policy

| Field | Value |
|---|---|
| **ID** | TC-SG-012 · **Suite** B · **Type** Positive · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** the recursive `TenantMixin` walk discovers a tenant table the migrations FORGOT to protect (RLS
disabled or `org_isolation` absent) — the exact regression the test claims to catch — but reality has an unprotected table.

**Command**
```
docker compose exec -T backend python - <<'PY'
# import app.identity.models, recursively collect TenantMixin.__subclasses__(),
# then for each table query pg_class.relrowsecurity + pg_policies for org_isolation; assert both.
PY
```
**Evidence**
```
DISCOVERED tenant tables: ['support_grant', 'users']
  support_grant: relrowsecurity=True  org_isolation_policy=PRESENT
  users:         relrowsecurity=True  org_isolation_policy=PRESENT
ASSERT-OK: every discovered tenant table has RLS enabled + org_isolation policy
```
**Verdict:** Defense holds. The dynamic enumeration (mirroring `_all_tenant_models`' recursive walk) yields exactly
`{support_grant, users}` — no tenant table is missing its RLS protection in the live DB. Enumeration set matches the
migration coverage 1:1.
