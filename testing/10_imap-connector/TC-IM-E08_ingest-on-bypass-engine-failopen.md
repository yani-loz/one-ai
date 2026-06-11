# TC-IM-E08 — Ingest path runs on the BYPASSRLS global engine (latent fail-open surface)

| ID · Suite · Type · Mode |
|---|
| TC-IM-E08 · E (Persistence/RLS/entity graph) · Adversarial · ingest |

| Result · Tag · Severity · Status |
|---|
| ⚠️ Pass-with-concern · 🆕 (latent fail-open flag) · Medium · Executed |

## Objective
Characterize precisely which ingest paths exercise RLS and which do not.
**Production today is enforced:** the `connector_sync_runner` (`connectors/sync/connector_sync_runner.py`)
opens its own `scoped_session(org_id)` on the tenant engine (the NOBYPASSRLS `oneai_app` role with the
org GUC bound). The dev/test paths are NOT: `EmailIngestService` is engine-agnostic, and both the
ingest-service tests' conftest and the disk dump driver (`scripts/ingest_imap_dump.py`) construct it
on `GlobalSessionLocal` — the BYPASSRLS `oneai_global` engine — so RLS does not bite there.

## Break hypothesis
Because `EmailIngestService` takes whatever session it is handed, a future production path that wires
ingest to the global engine (or any GUC-unset session) would write tenant email with **no RLS
backstop** — a fail-open surface. The dev/test + dump-driver paths already run this way.

## Steps
1. **Bypass path:** open `GlobalSessionLocal()` (no GUC set), seed a connection, ingest one email,
   commit; read back the row and read `current_user` + `current_setting('app.current_org_id', true)`.
2. **Enforced path:** open `scoped_session(org)` and read `current_user` + the GUC; confirm it is the
   `oneai_app` tenant role with the GUC bound, and a read of an unrelated org returns zero.

## Expected
NEW Med (flag), with the prod-path nuance stated: dev/test+driver = BYPASS; prod runner = enforced.

## Execution result (2026-06-09)
Harness: `testing/10_imap-connector/harness/entity_resolution_suite.py` (case E08)

```
  [PASS] e08_ingest_on_bypass_engine_no_rls :: ingest engine user='oneai_global' guc=None; wrote+read 1 msg with NO GUC (BYPASSRLS -> RLS does NOT bite on the dev/test ingest path)
  [PASS] e08_prod_runner_path_is_scoped_enforced :: prod scoped_session user='oneai_app' guc='4cecb620-...-03a745925b95' (NOBYPASS tenant role, GUC bound) — the runner path DOES enforce RLS; the gap is dev/test+dump-driver only
```

Code evidence:
- `connectors/sync/connector_sync_runner.py:152,317,373` — prod runner uses `async with scoped_session(org_id)`.
- `tests/connectors/imap/services/conftest.py:78` + `scripts/ingest_imap_dump.py:33,82` — both build
  `EmailIngestService` on `GlobalSessionLocal` (BYPASSRLS).

**Verdict:** ⚠️ **Pass-with-concern.** **The production sync path enforces RLS today** (scoped tenant
role + bound GUC, live-confirmed) — so this is NOT an active cross-tenant leak, and the
"cross-tenant exposure is never below High" floor does not apply. The finding is a **latent
architectural fail-open surface**: `EmailIngestService` is engine-agnostic, the app-layer org filter
is the only control on the global engine, and the dev/test + dump-driver paths already run on BYPASS.
If a future prod path mis-wires ingest to the global engine, a single missed `WHERE org_id` would
leak cross-org with no DB backstop.

**Tag:** 🆕 NEW · **Severity: Medium (flag, latent).** Not a distinct FIX_BEFORE_PROD line item,
though it is adjacent to the RLS-enforcement work. Suggested hardening: have `EmailIngestService`
(and the dump driver) run on `scoped_session(org_id)` so the dev/test path exercises the same
enforced engine as prod — closing the asymmetry the docstring (`database.py:13-18`) already warns
about ("a tenant flow wrongly on the global engine fails open/silent").
