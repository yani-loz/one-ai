# One AI — MVP

> *One Company. One AI.* — an enterprise AI that becomes a company's central nervous
> system. See [`docs/Project_Bible.md`](docs/Project_Bible.md) for the full vision and
> architecture. This README covers running the scaffold locally.

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 19 · Vite 6 · TypeScript · Tailwind v4 · Framer Motion |
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · `uv` |
| Database | PostgreSQL 16 + pgvector |
| Migrations | Alembic (async) |
| Dev runtime | Docker Compose (everything containerized, hot-reload) |

## Prerequisites

- Docker Desktop (running)
- For working outside containers: `uv`, Node 22, `pnpm` 10

## Quick start

```bash
cp .env.example .env        # adjust if needed; defaults work out of the box
docker compose up --build
```

Then:

- Frontend → http://localhost:5173 (shows a live backend/DB status panel)
- API docs → http://localhost:8000/docs
- Health → http://localhost:8000/health (200 means DB is reachable)

The backend container runs `alembic upgrade head` on start (enabling pgvector),
then launches uvicorn with `--reload`. Editing files under `backend/` or
`frontend/src/` hot-reloads inside the containers.

## Project layout

```
backend/    FastAPI app (app/), Alembic migrations (app/db/migrations), tests/
frontend/   React + Vite app (src/), Tailwind v4 design system (src/index.css)
docker-compose.yml   dev stack: db + backend + frontend
scripts/    repo tooling (file-size ceiling gate)
docs/       Project Bible + experiments notebook
.claude/    coding / design / security / testing rules (auto-loaded)
```

## Common commands

```bash
# Backend (inside the container — has DB access)
docker compose exec backend uv run pytest
docker compose exec backend uv run ruff check .
docker compose exec backend uv run alembic revision --autogenerate -m "add X"

# Frontend
docker compose exec frontend pnpm test
docker compose exec frontend pnpm lint

# Tear down (keep data) / wipe the database volume
docker compose down
docker compose down -v
```

## Demo data (dev)

A DEV-ONLY seed script populates a fixed demo organization (`One AI Demo GmbH`,
slug `demo`) with a platform admin, a company admin, and a member. It is
**idempotent** — safe to re-run; existing rows are skipped — and refuses to run
when `APP_ENV=production`.

```bash
# With the stack up (DB reachable), run the seed inside the backend container:
docker compose exec backend uv run python -m scripts.seed_identity
```

> ⛔ **The seeded accounts are public backdoors and MUST be removed before
> production.** The three demo credentials (and the fixed demo org) are listed in
> [`docs/FIX_BEFORE_PROD.md`](docs/FIX_BEFORE_PROD.md), which tracks every place
> they live and the steps to delete them — including this seed script. Never run
> this seed against a real environment.

## Tenant isolation

`org_id` is the canonical tenant key. Every tenant-scoped model mixes in
`TenantMixin` (org_id NOT NULL + indexed); `get_tenant_session` binds each DB
session to the active org via `set_config('app.current_org_id', …)`, the seam
for Postgres Row-Level Security once the first org-scoped table lands. Real
auth (JWT/RBAC/SSO) is deferred to Phase 4 — see Project Bible §13.

## Production

Both Dockerfiles are multi-stage. The `prod` targets build a hardened, non-root
backend (multi-worker uvicorn) and an nginx-served static frontend:

```bash
docker build --target prod ./backend
# VITE_API_URL is inlined at build time; pass your real API origin. Serves on :8080 (non-root nginx).
docker build --target prod --build-arg VITE_API_URL=https://api.example.com ./frontend
```
