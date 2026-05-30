# Testing rules

## When to read this
Writing tests, modifying tests, or building functionality that needs tests. Auto-loaded into every session.

---

Tests are how we prove the system does what we promised — and the only way we catch the multi-tenancy / contract-defense bugs before they cost us the contract. The rules below trade test-writing speed for production confidence; we choose confidence.

---

## Runners + coverage

- **Backend:** `pytest` + `pytest-asyncio` + `pytest-cov`. Run from `backend/`: `pytest --cov`.
- **Frontend (when scaffolded):** `vitest` + `@vitest/coverage-v8` + `@testing-library/react`. Run from `frontend/`: `npm run test`.
- **IMPORTANT: Coverage threshold is 70%.** CI fails below this. PRs cannot merge with coverage drops below threshold.
- Coverage is a floor, not a target. 70% with the right tests beats 95% with mock-heavy tests that exercise nothing.

---

## The test pyramid

Write many unit tests, fewer integration tests, very few e2e tests.

| Layer | What it covers | Externals |
|---|---|---|
| **Unit** | Single function / class / module in isolation | Mock everything outside the unit (adapters, DB, network) |
| **Integration** | Multiple internal modules talking to a real internal DB | Real DB (testcontainers); mock external vendors |
| **E2E** | Full critical-path user flow (login → upload → analyze → generate → export) | Real-ish workflow; mock Claude SDK + GBS storage at adapter boundary |

Reach for the cheapest layer that gives you confidence. If a unit test catches the bug, you don't need an integration test for the same thing.

---

## The non-negotiable — multi-tenancy

**YOU MUST write a cross-tenant negative test for every tenant-scoped endpoint, service, and repository method.** This protects clause §З and Annex 2 §4.9 — a cross-company data leak is a contract breach.

Pattern:
- Authenticate as tenant A.
- Attempt to read / modify a resource belonging to tenant B.
- Assert the response is **404 or 403** (never 200 with empty body — that leaks existence).
- Assert NO B-tenant data appears in the response under any field.

If you write a tenant-scoped endpoint without this test, code review rejects the PR. This is the hardest rule in the test suite.

---

## Mocking — the boundary rule

- **Mock vendor adapters at the adapter boundary** (the wrapper file in `backend/adapters/<vendor>/`), NEVER inside business logic.
- **Do not mock internal services or repositories** in unit tests for code that uses them — those should be real. If isolation forces you to mock internal code, you are testing in the wrong layer.
- **Do not mock framework code** (FastAPI route registration, Pydantic field validators, Tortoise's `_meta`). Frameworks test themselves.
- **Mocks must verify contract**, not behavior. Assert that the adapter was called with the expected shape; do not assert that the assertion was made.

If a test passes purely through mocked behavior (no real code executed), delete it — it tests nothing.

---

## Test structure

- **File path mirrors source path.** `backend/services/auth/login.py` → `backend/tests/services/auth/test_login.py`. `frontend/src/components/SectionEditor.tsx` → `frontend/src/components/SectionEditor.test.tsx`.
- **Test naming:** `test_<what>_<condition>_<expected>`. Examples: `test_login_invalid_password_returns_401`, `test_generate_section_kss_quantity_in_text_returns_validation_error`.
- **AAA pattern (Arrange, Act, Assert)** — blank lines between the three blocks. One assertion per test where possible.
- **FIRST principles:** Fast (sub-second per unit test), Isolated (any order works), Repeatable (no random data without seed), Self-validating (no manual inspection), Timely (written with the code, not after).

---

## Fixtures

- **Use `conftest.py` at the appropriate scope level.** Function-scoped for short-lived data; session-scoped for expensive setup (DB schema, app instance).
- **Share via pytest fixtures, NOT module-level imports.** Imports create coupling between test files; fixtures don't.
- **Seed data is explicit in the test, not hidden in fixtures.** A reader should see what the test depends on without opening conftest.

---

## AI-generated content tests

When testing functionality that calls the Claude SDK to generate ТП text:

- **Mock the Claude SDK adapter** (e.g., `claude_adapter.generate(...)`) — never call real Claude in unit/integration tests.
- **Verify the rules the SDK should follow** at the test level: regex-check generated output for future tense (no past-tense verbs), validator-check Section 2 text for the absence of КСС-style quantity rows, count provenance colors per sentence.
- The **rules themselves** (KCC, tense, generation modes, color application) live in `backend/ai/` system prompts. Tests verify the rules took effect; they do not define the rules.

---

## Quick reference

| Rule | Concrete |
|---|---|
| BE runner | `pytest --cov` (≥70% threshold) |
| FE runner | `npm run test` (Vitest, ≥70%) |
| Cross-tenant negative test | required per tenant-scoped endpoint / service / repo |
| Mock location | adapter boundary only |
| File location | mirror source tree |
| Naming | `test_<what>_<condition>_<expected>` |
| Pattern | AAA, FIRST |
| Flaky test | `xfail` + Jira ticket, never retry-loop |
| Coverage threshold change | requires Stilyana + Sasho sign-off |
