# Target 09 — Secrets gate + RLS standing-invariant (`SG`)

> **Scope.** Adversarial validation of the two standing-invariant controls shipped in `f8a4fbd` (+ `3415ce1`):
> the **fail-closed secrets gate** (`backend/app/core/config.py`) and the **RLS standing-invariant test**
> (`backend/tests/identity/models/test_rls_invariants.py`). Companion static record:
> [`docs/audits/2026-06-02_secrets-gate-rls-invariant-dynamic-adversarial.md`](../../docs/audits/2026-06-02_secrets-gate-rls-invariant-dynamic-adversarial.md).

## What we are testing (and what we are NOT)

This target validates that **(a)** the boot gate refuses to start outside `{local, test}` while a known dev-default
secret is in place, and **(b)** the invariant test actually *has teeth* — it runs (not skips) and goes RED when a
tenant table loses its `org_isolation` policy.

It does **NOT** validate runtime RLS row-filtering. DB-level enforcement is **inert by design** today: the app
connects as superuser/owner `oneai`, `relforcerowsecurity=f`, and the role/engine flip (`FORCE` + a non-superuser
role) is deferred to migration `0007`. Suite C proves that boundary **once** (CONFIRMS-DOCUMENTED), per the
`testing/README.md` "do not dwell on documented deferrals" rule.

## Environment

| Item | Value |
|---|---|
| Stack | `docker compose up` — live `db` / `backend` (:8000) / `frontend` (:5173) |
| Live config | `app_env=local`, `jwt_secret='dev-only-insecure-secret-change-me-in-prod'`, `postgres_password='oneai'` (forgeable, `requires_secure_secrets=False`) |
| Boot-gate tests | run in **separate one-shot** `docker compose exec` processes (never touch the running uvicorn) |
| Teeth proof | **throwaway scratch DBs** (`oneai_sg_*`), provisioned via `alembic upgrade head`, each dropped after |
| Non-destructive | read-only on the real `oneai` DB; no writes under `backend/`/`frontend/`; `super@ethera.ai` untouched |

Reusable helpers + the exact recipes live in [`harness/_common.py`](harness/_common.py).

## Status dashboard

Legend — Result: ✅ pass · ⚠️ pass-with-concern · ❌ fail. Tag: ✔ CONFIRMS-FIXED · 🆕 NEW · 📋 CONFIRMS-DOCUMENTED · — NA.

### Suite A — JWT fail-closed gate

| ID | Case | Result | Tag |
|---|---|---|---|
| [TC-SG-001](TC-SG-001_positive-control-names-offending-secret.md) | Positive control: prod+dev-JWT names `JWT_SECRET`; inverse names `POSTGRES_PASSWORD` | ✅ | ✔ |
| [TC-SG-002](TC-SG-002_env-var-boot-overrides-dotenv.md) | End-to-end env-var boot fails closed; `APP_ENV=production` overrides `.env` (exit 1) | ✅ | ✔ |
| [TC-SG-003](TC-SG-003_staging-generalization.md) | Staging + dev-JWT RAISES (the headline gap closed) | ✅ | ✔ |
| [TC-SG-004](TC-SG-004_typo-and-trailing-space-fail-closed.md) | Typo / `'Production '` fail closed (`.lower()` not `.strip()`) | ✅ | ✔ |
| [TC-SG-005](TC-SG-005_exempt-envs-preserved.md) | `local`/`test`/`LOCAL` boot with dev secrets | ✅ | ✔ |
| [TC-SG-006](TC-SG-006_blank-secret-bypasses-denylist.md) | **NEW** — blank prod secret bypasses the exact-match denylist → forgeable token | ⚠️ | 🆕 |
| [TC-SG-007](TC-SG-007_leading-space-env-footgun.md) | `' local'` RAISES — safe (fail-closed) but an operator ergonomics trap | ⚠️ | — |

### Suite B — RLS invariant test: teeth + live-catalog truth

| ID | Case | Result | Tag |
|---|---|---|---|
| [TC-SG-010](TC-SG-010_test-runs-with-teeth-not-skipped.md) | Invariant test runs with teeth on the migrated dev DB (0 skipped) | ✅ | ✔ |
| [TC-SG-011](TC-SG-011_live-catalog-truth.md) | Live catalog: `relrowsecurity=t`, `relforcerowsecurity=f`, `org_isolation` qual correct | ⚠️ | 📋 |
| [TC-SG-012](TC-SG-012_enumeration-completeness.md) | Every discovered tenant table has live RLS + policy | ✅ | ✔ |
| [TC-SG-013](TC-SG-013_faithful-teeth-proof-red.md) | **Faithful teeth proof**: forgotten policy on `support_grant` turns the REAL test RED | ✅ | ✔ |
| [TC-SG-014](TC-SG-014_skip-on-non-migrated.md) | LOUD skip on a pre-migration DB (no false pass) | ✅ | ✔ |
| [TC-SG-015](TC-SG-015_sentinel-blind-spot-skips-not-fails.md) | **NEW** — forgotten policy on the sentinel `users` SKIPs instead of FAILs | ⚠️ | 🆕 |
| [TC-SG-016](TC-SG-016_anti-vacuous-guard-import.md) | Anti-vacuous guard A: empty enumeration → `assert tenant_tables` fires | ✅ | ✔ |
| [TC-SG-017](TC-SG-017_anti-vacuous-guard-content-blind.md) | Anti-vacuous guard B: stray `TenantMixin` on `audit_log` → `isdisjoint` fires | ✅ | ✔ |

### Suite C — RLS still inert at runtime (prove-once)

| ID | Case | Result | Tag |
|---|---|---|---|
| [TC-SG-020](TC-SG-020_superuser-bypasses-rls.md) | Superuser app role bypasses `org_isolation` (GUC pinned to A, org-B rows visible) | ⚠️ | 📋 |
| [TC-SG-021](TC-SG-021_forged-token-cross-org-metadata.md) | Forged dev-secret platform token → 200 cross-org metadata; unknown-secret control → 401 | ⚠️ | 📋 |
| [TC-SG-022](TC-SG-022_boundary-statement.md) | Boundary: `f8a4fbd` is a boot-time control, not a runtime RLS enabler | ⚠️ | 📋 |

## Verdict

18 cases · **0 ❌ fails** — both controls hold for their stated contracts · **2 🆕 NEW** (both Low, both
beyond-contract: TC-SG-006 blank-secret bypass, TC-SG-015 sentinel blind spot) · RLS-inert runtime characterized
once. Remediations tracked in the audit §7 and `docs/FIX_BEFORE_PROD.md`.
