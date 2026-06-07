# Repository Guidelines

## Project Structure & Module Organization

This is a two-service MVP. `backend/` contains the FastAPI app in `backend/app/`, database code in `backend/app/db/`, scripts in `backend/scripts/`, and Pytest tests in `backend/tests/`. `frontend/` contains the React 19 + Vite app in `frontend/src/`, with feature folders such as `identity/`, `platform/`, `support/`, and reusable UI in `components/`. Root-level `docs/` holds architecture notes, `testing/` holds QA plans/evidence, and `scripts/` contains repository tooling.

## Build, Test, and Development Commands

Use Docker Compose as the default local runtime:

```bash
cp .env.example .env
docker compose up --build
```

The frontend runs at `http://localhost:5173`; API docs run at `http://localhost:8000/docs`.

Common commands:

```bash
docker compose exec backend uv run pytest
docker compose exec backend uv run ruff check .
docker compose exec backend uv run alembic revision --autogenerate -m "add feature"
docker compose exec frontend pnpm test
docker compose exec frontend pnpm lint
docker compose down
```

For frontend-only work outside containers, run from `frontend/` with `pnpm`.

## Coding Style & Naming Conventions

Backend code targets Python 3.12 and is formatted/linted by Ruff with 100-character lines. Prefer typed FastAPI dependencies, async SQLAlchemy patterns, and `snake_case` module names. Frontend code is TypeScript ESM using React components in `PascalCase`, hooks/utilities in `camelCase`, and feature-local tests. Use Prettier and ESLint for frontend formatting and checks.

## Testing Guidelines

Backend tests use Pytest with `pytest-asyncio`; keep coverage at or above the configured 70% threshold. Frontend tests use Vitest, Testing Library, and V8 coverage; name tests `*.test.ts` or `*.test.tsx` near the code they verify. Add tests for auth, tenant isolation, RLS/security gates, and user-facing flows touched by the change.

## Commit & Pull Request Guidelines

Git history uses scoped conventional commits, for example `test(qa): ...`, `harden(config): ...`, and `docs(plan): ...`. Keep commits focused and use an imperative summary. Pull requests should include a concise description, linked issue or testing target when available, commands run, and screenshots for UI changes. Note migrations, configuration changes, and any deferred risk explicitly.

## Security & Configuration Tips

Copy `.env.example` to `.env` and do not commit real secrets. Demo seed credentials are development-only; do not run seed scripts against production-like environments. Preserve fail-closed behavior around JWT secrets, tenant scoping, and RLS invariants.
