# Testing rules

## When to read this
Writing tests, modifying tests, or building functionality that needs tests. Auto-loaded into every session.

---

Tests are how we prove the system does what we promised — and the only way we catch the multi-tenancy / contract-defense bugs before they cost us the contract. The rules below trade test-writing speed for production confidence; we choose confidence.

---

## Runners + coverage

- **Backend:** `pytest` + `pytest-asyncio` + `pytest-cov`. Run from `backend/`: `pytest --cov`.
- **Frontend:** `vitest` + `@vitest/coverage-v8` + `@testing-library/react`. Run from `frontend/`: `pnpm test` (the repo is pnpm-only — `package.json:6` pins `packageManager: pnpm@10.28.2`).
- **IMPORTANT: Coverage threshold is 70%.** CI fails below this. PRs cannot merge with coverage drops below threshold.
- Coverage is a floor, not a target. 70% with the right tests beats 95% with mock-heavy tests that exercise nothing.
- **Backend tests need a live Postgres**, not an in-process fake: point `POSTGRES_HOST` / `POSTGRES_PORT` (`app/core/config.py:76-77`) at the database the compose stack runs (`docker-compose.yml` service `db`). The DB-backed tests ERROR without it.

---

## The test pyramid

Write many unit tests, fewer integration tests, very few e2e tests.

| Layer | What it covers | Externals |
|---|---|---|
| **Unit** | Single function / class / module in isolation | Mock everything outside the unit (adapters, DB, network) |
| **Integration** | Multiple internal modules talking to a real internal DB | The live Postgres the compose stack provides (`docker-compose.yml` service `db`) — testcontainers is not a dependency; mock external vendors |
| **E2E** | Full critical-path user flow (login → create a connection → ingest → person-bound read) | Real-ish workflow; mock the reader model and IMAP at the adapter boundary |

Reach for the cheapest layer that gives you confidence. If a unit test catches the bug, you don't need an integration test for the same thing.

---

## The non-negotiable — multi-tenancy

**YOU MUST write a cross-tenant negative test for every tenant-scoped endpoint, service, and repository method.** A cross-company data leak is a GDPR incident and a customer-contract breach — it is the failure One AI cannot survive.

Pattern:
- Authenticate as tenant A.
- Attempt to read / modify a resource belonging to tenant B.
- Assert the response is **404 or 403** (never 200 with empty body — that leaks existence).
- Assert NO B-tenant data appears in the response under any field.

If you write a tenant-scoped endpoint without this test, code review rejects the PR. This is the hardest rule in the test suite.

---

## Mocking — the boundary rule

- **Mock vendor adapters at the adapter boundary** — the domain's own `adapters/` wrapper file; today the only one is `backend/app/ask/adapters/together_chat.py`. NEVER inside business logic.
- **Do not mock internal services or repositories** in unit tests for code that uses them — those should be real. If isolation forces you to mock internal code, you are testing in the wrong layer.
- **Do not mock framework code** (FastAPI route registration, Pydantic field validators, SQLAlchemy 2.0 mapper internals). Frameworks test themselves.
- **Mocks must verify contract**, not behavior. Assert that the adapter was called with the expected shape; do not assert that the assertion was made.

If a test passes purely through mocked behavior (no real code executed), delete it — it tests nothing.

---

## Test structure

- **File path mirrors source path.** `backend/app/identity/services/auth_service.py` → `backend/tests/identity/services/test_auth_service.py`. `frontend/src/components/insignia/generateInsignia.ts` → `frontend/src/components/insignia/generateInsignia.test.ts`.
- **Test naming:** `test_<what>_<condition>_<expected>`. Examples: `test_login_wrong_password_raises_invalid_credentials`, `test_cross_tenant_promotion_probe_returns_not_found`.
- **AAA pattern (Arrange, Act, Assert)** — blank lines between the three blocks. One assertion per test where possible.
- **FIRST principles:** Fast (sub-second per unit test), Isolated (any order works), Repeatable (no random data without seed), Self-validating (no manual inspection), Timely (written with the code, not after).

---

## Fixtures

- **Use `conftest.py` at the appropriate scope level.** Function-scoped for short-lived data; session-scoped for expensive setup (DB schema, app instance).
- **Share via pytest fixtures, NOT module-level imports.** Imports create coupling between test files; fixtures don't.
- **Seed data is explicit in the test, not hidden in fixtures.** A reader should see what the test depends on without opening conftest.

---

## Ask-layer tests (model calls + security gates)

When testing anything under `backend/app/ask/` — the retrieval tools, the agent runner, the SQL pipeline:

- **Mock the reader adapter** at `backend/app/ask/adapters/` (`together_chat.py`) — **never call Together, or any hosted model, from a unit or integration test.** No test may depend on a network round trip to a model provider.
- **The database side stays real.** Tool SQL is tested against the live Postgres through `reader_session(org_id, person_id)`, because what is being proved is what the *policies* return, not what a mock returns. A mocked read plane proves nothing about tenant or per-person isolation.
- **The security gates are scripts, not pytest.** Three of them, run from `backend/`:
  - `python -m scripts.ask_loop.conformance` — behavioural conformance of the tool layer.
  - `python -m scripts.ask_loop.seal_check` — the outcome seals; exits non-zero if any seal is broken.
  - `python -m scripts.ask_loop.defence_matrix` — the causal half of the seal: which mechanism actually stops which attack (`ci.yml:63`). A green `seal_check` is an outcome, not a proof of cause, so do not treat it alone as sufficient.
  Run all three by hand after any change to `sql_guard.py`, `sql_execution.py`, the tool registry or the reader RLS policies. **As of 2026-09-06 CI does not run them** — the steps at `.github/workflows/ci.yml:59,62,69` are an uncommitted working-tree edit (absent from `git show HEAD:.github/workflows/ci.yml`). The only gate that *is* committed at HEAD is the file-size job, `scripts/check_file_size.py` (`ci.yml:17`).

---

## Quick reference

| Rule | Concrete |
|---|---|
| BE runner | `pytest --cov` (≥70% threshold) |
| FE runner | `pnpm test` (Vitest, ≥70%) |
| Cross-tenant negative test | required per tenant-scoped endpoint / service / repo |
| Mock location | adapter boundary only (`backend/app/<domain>/adapters/`) |
| Ask security gates | `scripts.ask_loop.conformance` · `seal_check` · `defence_matrix` — run by hand (not in CI as of 2026-09-06) |
| File location | mirror source tree |
| Naming | `test_<what>_<condition>_<expected>` |
| Pattern | AAA, FIRST |
| Flaky test | `xfail` + tracked ticket, never retry-loop |
| Coverage threshold change | requires founder sign-off |
