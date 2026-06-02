# TC-SG-017: Anti-vacuous guard B (content-blindness) — a stray TenantMixin on a platform table (audit_log) trips isdisjoint

| Field | Value |
|---|---|
| **ID** | TC-SG-017 · **Suite** B · **Type** Negative · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** a content-blind platform table (`audit_log` / `organizations` / `platform_admins` / `refresh_tokens`)
accidentally mixing in `TenantMixin` would be swept into the tenant set and silently treated as tenant-scoped — the
disjointness guard does not actually fire on it.

**Command**
```
docker compose exec -T backend python - <<'PY'
# import real identity models; define class StrayTenantOnAuditLog(TenantMixin) with __table__.name='audit_log';
# run the test's three assertions incl. tenant_tables.isdisjoint(_KNOWN_NON_TENANT_TABLES)
PY
```
**Evidence**
```
tenant_tables WITH stray audit_log mix-in: ['audit_log', 'support_grant', 'users']
GUARD-B FIRED (content-blind protects): A non-tenant platform table mixed in TenantMixin: {'audit_log'}
```
**Verdict:** Guard B has teeth. Injecting a stray `TenantMixin` subclass whose table is the platform table `audit_log`
makes `tenant_tables.isdisjoint(_KNOWN_NON_TENANT_TABLES)` False, raising the documented `AssertionError` naming the
offending table. The content-blind platform plane is actively protected from being mis-classified as tenant-scoped.
