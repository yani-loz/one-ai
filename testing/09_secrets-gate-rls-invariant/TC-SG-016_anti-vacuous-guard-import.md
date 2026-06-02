# TC-SG-016: Anti-vacuous guard A (import-vacuity) — empty enumeration without identity import fails `assert tenant_tables`

| Field | Value |
|---|---|
| **ID** | TC-SG-016 · **Suite** B · **Type** Negative · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** if identity models are never imported, `TenantMixin.__subclasses__()` is empty and the per-table RLS
assertions pass VACUOUSLY (zero iterations) — a silent no-op masquerading as green; the non-vacuity guard does not actually
catch this.

**Command**
```
docker compose exec -T backend python - <<'PY'
# import ONLY app.common.base_model.TenantMixin (NOT app.identity.models);
# build tenant_tables via the recursive walk; run `assert tenant_tables, ...`
PY
```
**Evidence**
```
tenant_tables WITHOUT identity import: set()
GUARD-A FIRED (non-vacuity protects): AssertionError: TenantMixin enumeration is empty — identity models not imported; the RLS invariant ...
```
**Verdict:** Guard A has teeth. With identity models unimported, the recursive walk yields an empty set and
`assert tenant_tables` raises the documented `AssertionError` — the vacuous-pass scenario is actively caught, not just
commented. (In the real test, line 27 imports identity models, so this stays green there — TC-SG-010.)
