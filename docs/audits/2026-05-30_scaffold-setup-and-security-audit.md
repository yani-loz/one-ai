# One AI MVP Scaffold — Setup & Security Audit

> **Date:** 2026-05-30 · **Method:** multi-agent workflow (4 dimension reviewers → adversarial verification of every finding → synthesis). 24 agents, 19 raw findings, 9 confirmed (10 refuted). **0 critical / 0 high / 0 medium.**

**Overall verdict:** For a clean-slate dev scaffold the One AI MVP is correctly set up and appropriately secured for its stage, with zero critical/high/medium findings; tenant isolation is correctly stubbed-but-not-yet-enforced (`X-Org-Id` header, no RLS, no tenant tables — a documented Phase-4 deferral), and the only issues are 8 low + 1 info hardening and enforcement-asymmetry gaps worth fixing before features and the first deploy land.

---

## 1. Verdict

The scaffold is **correctly set up and secured for its stage**, and meets the spirit of the "maximum production-ready" bar to the extent that a no-feature-code base allows: **0 critical, 0 high, 0 medium** findings survived adversarial verification — only 8 low + 1 info, all latent hardening or enforcement-asymmetry gaps rather than live defects. The dev stack renders and runs reproducibly (`docker compose config` clean, both lockfiles consistent, async Alembic + pgvector migration sound, Windows hot-reload correctly wired), the code obeys the repo's own A1–A5 quality rules with near-zero deviation, and the container posture is strong (non-root backend, tag-pinned bases, frozen installs, `.env` outside every build context). The one honesty caveat that must not be blurred: **tenant isolation is stubbed, not live-enforced.** Tenant identity comes from the client-controlled, trivially spoofable `X-Org-Id` header; there are no RLS policies and no tenant-scoped tables yet. This is a **documented Phase-4 deferral** (JWT/RBAC replaces the header; RLS lands with the first `org_id` table), so it is neither a pass nor a failure today — but it means the "hardest rule" is wired as a seam, not yet enforced, and the seam has one latent fail-open defect to fix while it is still being authored.

---

## 2. Setup correctness (verified working)

| Area | Status |
|---|---|
| **Compose render** | `docker compose config` renders with no errors/warnings (project `one-ai-mvp`, three services db/backend/frontend). |
| **DB service** | `pgvector/pgvector:pg16`, `pg_isready` healthcheck (5s/12 retries/10s start_period), named `db-data` volume for persistence. |
| **Startup ordering** | `depends_on: db → condition: service_healthy` — backend waits for Postgres before running migrations. `frontend → backend: service_started` is correct by design (static dev server; only the browser calls `/health`). |
| **Backend Dockerfile** | Multi-stage `base → deps-dev/deps-prod → dev/prod`; layer-cache order copies `pyproject.toml`+`uv.lock` before source; prod stage is non-root multi-worker. `uv` pinned `ghcr.io/astral-sh/uv:0.9.29`. |
| **Frontend Dockerfile** | Multi-stage `base → deps → dev/build/prod`; prod serves built `dist` via `nginx:1.27-alpine` with SPA `try_files` fallback. |
| **venv-outside-bind-mount** | `UV_PROJECT_ENVIRONMENT=/opt/venv` + `PATH` ensure the `/app` host mount never shadows installed deps (the classic Windows-compose footgun, correctly avoided). |
| **node_modules mask** | Anonymous volume `/app/node_modules` prevents the host bind mount from shadowing image deps. |
| **Hot-reload (Windows)** | `WATCHFILES_FORCE_POLLING=true` for uvicorn `--reload`; Vite `server.watch.usePolling:true` + `host:true`. Both sides wired. |
| **Alembic** | Async `env.py` uses asyncpg (same driver as app), pulls DB URL from `app.core.config`, `target_metadata=Base.metadata`, `NullPool` for migrations. Migration `0001_enable_pgvector` runs `CREATE EXTENSION IF NOT EXISTS vector` idempotently. Dev start runs `alembic upgrade head`. |
| **Lockfiles** | `backend/uv.lock` and `frontend/pnpm-lock.yaml` present, consistent with manifests, not gitignored → frozen installs will not fail. |
| **Version pinning** | `.python-version` 3.12 matches `requires-python`, Dockerfile `python:3.12-slim`, and CI. |
| **CI** | Mirrors the local stack: pgvector service for the backend job, uv 0.9.29 + pnpm 10.28.2 pinned to match images, frozen installs + `alembic upgrade head` + tests/build. |
| **Env consistency** | `.env` and `.env.example` share keys; README quickstart and ports (5173/8000/health) match the real files. |

No blocker would break `docker compose up` on a fresh machine.

---

## 3. Security posture — mapped to `security.md`

**Tenant isolation (the 4 layers — "hardest rule"):**

| Layer | Required | Current state |
|---|---|---|
| 1. PostgreSQL `org_id` NOT NULL | Yes | **Satisfied at the column level** — `TenantMixin` defines `org_id` NOT NULL + indexed. No concrete table mixes it in yet, so the live DB column is correctly deferred to the first tenant table. |
| 2. Row-Level Security policies | "defined in prototype" | **Correctly deferred** — zero ORM models exist, so no table requires an RLS policy yet. RLS lands with the first `org_id` table. |
| 3. Application-level tenant scoping | Yes | **Seam built, not yet enforcing** — `get_tenant_session()` sets the `app.current_org_id` GUC via parameterized `set_config()` inside the transaction; proven by `test_database.py`. |
| 4. API Gateway tenant context from JWT | Phase 4+ | **Deferred to Phase 4** — today the org comes from the client-controlled `X-Org-Id` header (spoofable). `resolve_org_id` refuses a missing tenant in production and validates the header is a real UUID, but identity is not yet authenticated. |

**Net:** isolation is **stubbed, not live-enforced**. Until JWT replaces `X-Org-Id` and RLS policies exist, there is no enforced cross-tenant boundary — but there is also no tenant data, no auth surface, and no RLS to bypass, so **no critical live-exploitable issue exists today.**

**Secrets:** `.env` is gitignored AND untracked, lives at repo root outside both build contexts, and `.dockerignore` keeps it out of images. `.env.example` committed with placeholders. `config.py` is the sole `os.environ` reader and never logs secret values.

**CORS:** `allow_origins` is a restricted env-driven allow-list (not a wildcard), so `allow_credentials=True` is safe.

**Input/output:** parameterized tenant `set_config` (no SQL injection); server-generated UUID PKs; UUID-validated header; custom exception hierarchy; FastAPI debug off → no stack-trace leakage.

---

## 4. Confirmed findings (verified-real only)

All 9 survived adversarial verification; 10 other raw findings were refuted. No critical/high/medium.

| # | Severity | Finding | File | Fix |
|---|---|---|---|---|
| 1 | Low | **Tenant `set_config` is transaction-local but the request isn't pinned to one transaction** — any mid-request `commit()` resets `app.current_org_id`, so post-commit queries run unscoped (fail-open) once RLS is enabled. Harmless today (no RLS, no tenant tables, no writes). | `backend/app/core/database.py` | Re-apply `set_config` on each transaction via an `after_begin` event (or wrap the unit of work in one transaction). Add a write+commit→read regression test. |
| 2 | Low | **nginx prod image runs as root** — contradicts the backend (non-root) and the README "hardened, non-root" claim. | `frontend/Dockerfile` | Use `nginxinc/nginx-unprivileged:1.27-alpine` (uid 101, :8080); `listen 8080`. |
| 3 | Low | **Frontend prod build does not inject `VITE_API_URL`** — Vite inlines it at build time; absent, the bundle bakes the `localhost:8000` fallback → broken prod artifact. | `frontend/Dockerfile` | Add `ARG VITE_API_URL` + `ENV` to the build stage; pass via `--build-arg`. |
| 4 | Low | **Frontend 70% coverage threshold defined but never enforced** — thresholds set but coverage never collected; CI runs only `pnpm test`. Asymmetric with backend's `--cov-fail-under=70`. | `frontend/vite.config.ts`, `package.json` | Run `vitest run --coverage` so thresholds are checked. |
| 5 | Low | **A2 file-size gate skips `backend/tests/` and has no 300-line WARN** | `scripts/check_file_size.py` | Add test dirs to `SCAN_ROOTS`; add a non-fatal 300-line warning. |
| 6 | Low | **Frontend `.dockerignore` doesn't exclude `.env`** — holds only by accident today. | `frontend/.dockerignore` | Add `.env` / `.env.*`. |
| 7 | Low | **Test suite bundled into the prod backend image** — `prod COPY . .` includes `tests/`. Image bloat. | `backend/.dockerignore` | Add `tests/`. |
| 8 | Low | **`unknown` used in test glue without a justifying comment** (A4). | `frontend/src/App.test.tsx` | Add a one-line comment explaining the fetch-stub cast. |
| 9 | Info | **`BACKEND_PORT` and `VITE_API_URL` are decoupled in `.env`** — changing the port silently breaks the browser→API path. | `.env.example` | Add a coupling comment. |

---

## 5. Strengths (good precedents the scaffold sets)

- **Documentation-as-input (A4):** *every* source file carries the Role/Used by/Depends on/Key invariants docstring; every public function documented + type-hinted; no `Any`.
- **All files far under limits (A2):** largest backend file 72 lines, largest frontend 121 — well under the 300 soft target.
- **Layering & custom exceptions (A5):** routes parse+return only; `config.py` the sole env reader; `OneAIError` hierarchy, zero bare `raise Exception`, zero TODO/dead code.
- **Backend coverage gate is live** (`--cov-fail-under=70` in pytest addopts + CI).
- **Container hygiene:** non-root backend (correct chown-then-USER ordering), `--no-dev` prod venv, all bases tag-pinned (no `:latest`), frozen installs, `.env`/`.git` outside every build context.
- **Supply chain:** `pnpm audit` reports no known vulnerabilities; backend deps current.
- **Tenant seam correct where it exists:** parameterized `set_config`, UUID validation, production-fail-closed, `is_local=true` (right pooled-connection choice), integration test proving the GUC echoes back.
- **Frontend design language honored:** exact aurora `@theme` tokens, sanctioned glass + animations, `prefers-reduced-motion` block, no foreign brand colors.

---

## 6. Gaps & recommended next checks (completeness)

What could **not** be verified now and should be confirmed as features land:

1. **Cross-tenant negative test at the first tenant-scoped endpoint** — `testing.md`'s non-negotiable. Triggers the moment the first `org_id`-scoped route/service/repo exists.
2. **RLS policy authoring + `set_config` transaction pinning** — define `CREATE POLICY` per tenant table and enable RLS when the first `org_id` table lands; fix the transaction-pin defect at the same time, with a write+commit→read negative test.
3. **Replace `X-Org-Id` header with authenticated JWT tenant context (Phase 4)** — until then, tenant identity is client-spoofable.
4. **Backend Python dependency CVE scan in CI** — NOT run in this audit (requires an install); backend deps assessed by version inspection only. Add `pip-audit` / `uv`-native scanning alongside the already-clean `pnpm audit`.
5. **Production CORS origins review** before any real deploy.
6. **nginx non-root + prod `VITE_API_URL` injection** before the first frontend deploy.
7. **Enforcement-asymmetry cleanups** — FE coverage gate, A2 gate over `backend/tests/` + 300-line WARN, both `.dockerignore` files.

**Audit limitation:** dynamic verification (running the stack, hitting `/health`, executing the suites) was performed separately during the build, not re-run inside this synthesis — these findings rest on static/config inspection plus read-only validation (`docker compose config`, `pnpm audit`, `check_file_size.py`).

---

## 7. Remediation (2026-05-30, same day)

All 9 confirmed findings were fixed and verified immediately after the audit:

| # | Finding | Resolution | Verified by |
|---|---|---|---|
| 1 | Tenant scope fail-open on commit | `_bind_tenant_scope` re-applies the GUC via an `after_begin` event on every transaction | New test `test_get_tenant_session_scope_survives_commit` (write→commit→read still scoped) — passes |
| 2 | nginx prod ran as root | Switched to `nginxinc/nginx-unprivileged:1.27-alpine`, `listen 8080` | `docker run … id` → `uid=101(nginx)` |
| 3 | Prod build ignored `VITE_API_URL` | `ARG VITE_API_URL` + `ENV` in the build stage (default keeps dev parity) | `grep` of prod bundle shows the injected origin |
| 4 | FE coverage gate dormant | `test` script → `vitest run --coverage` (thresholds now enforced) | `pnpm test` reports "Coverage enabled", thresholds met |
| 5 | A2 gate skipped tests + no 300 WARN | Added `backend/tests` to scan roots + a non-fatal 300-line warning | `check_file_size.py` runs clean over the new scope |
| 6 | Frontend `.dockerignore` missing `.env` | Added `.env` / `.env.*` | — |
| 7 | Tests bundled into prod backend image | Added `tests/` to `backend/.dockerignore` | Backend prod image rebuilds |
| 8 | `unknown` cast without comment | Added a justifying comment in `App.test.tsx` | ESLint clean |
| 9 | `BACKEND_PORT`/`VITE_API_URL` decoupled | Coupling note added to `.env.example` | — |

Post-fix state: backend 11 tests / 84% coverage, frontend 3 tests with coverage gate live, ruff + eslint clean, both `prod` images build and run (non-root frontend, injected API origin). The four deferred items in §6 (cross-tenant test, RLS policies, JWT, backend CVE scan) remain correctly deferred to their respective phases.
