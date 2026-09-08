# MEM-01 Phase 0 Stage A — test-environment brief for the sealed-oracle author

This is the second of the three things the test author receives (contract, this brief, hard rules). It describes the environment the tests run in and the house idioms. It contains no implementation ideas for the instruments.

## 1. Where things are

- Repo root: `C:/Users/Yani_/Desktop/In-Progress/One AI/MVP`. Backend project: `backend/` (Python ≥3.12, `uv`, pytest with `asyncio_mode = "auto"`, ruff line length 100, `testpaths = ["tests"]`, coverage addopts `--cov=app --cov-fail-under=70` — code under `backend/tools/` is not in the coverage measure).
- Tests you write go under `backend/tests/tools/mem01_verify/` (mirror of `backend/tools/mem01_verify/`), with `backend/tests/tools/__init__.py` and `backend/tests/tools/mem01_verify/__init__.py` (one-line docstrings). Your own `conftest.py` lives at `backend/tests/tools/mem01_verify/conftest.py`.
- Run them from `backend/` with the oracle command: `POSTGRES_HOST=localhost POSTGRES_PORT=5432 uv run pytest tests/tools --no-cov -p no:cacheprovider -q`. `--no-cov` is mandatory — the project `addopts` carry `--cov=app --cov-fail-under=70`, which would fail the run on an unrelated tree. NEVER run bare `uv run pytest` (it collects other suites whose fixtures TRUNCATE the corpus in the dev database).
- Import paths: `from tools.mem01_verify import evid_norm` works when the cwd is `backend/` (pytest rootdir), exactly like the existing `from scripts.ask_loop import grade` in `tests/ask/services/test_harness_integrity.py`. Nothing configures `sys.path`; do not add path hacks.

## 2. The database rules (hard)

- The configured database (`settings.postgres_db`, today `oneai` on `one-ai-mvp-db-1` at 127.0.0.1:5432) holds the only copy of a 5,893-email corpus. **Your tests never SELECT, INSERT, TRUNCATE or otherwise touch its tables.** They may read `alembic_version` on it only if a test must prove an instrument refuses to write there.
- Your conftest must NOT import or request any fixture from `tests/access/conftest.py`, `tests/ask/conftest.py`, `tests/connectors/**`, `tests/entities/conftest.py`, or `tests/identity/conftest.py` — every one of those truncates tables. The only conftest above yours is the root `tests/conftest.py`, which defines `register_org`, `seed_org` (both write to the configured DB — do not use them), an autouse engine-dispose fixture, and an ASGI `client`.
- Tests that need a database use the instrument's probe-database surface from the contract (`tools.mem01_verify.probe_db.create_probe_database`, `tools.mem01_verify.db.probe_session_factories`) — a fresh database named `mem01_probe_…` on the same server, migrated to head, dropped after. Until the instrument exists those tests fail with the module-not-found reason (see §5). Roles `oneai_app`, `oneai_global`, `oneai_reader` exist cluster-wide with passwords from `backend/.env`; `Settings` reads `.env` relative to the cwd.
- Skip-loudly idiom when the server is unreachable: `pytest.skip("… — set POSTGRES_HOST/POSTGRES_PORT to the dev server")` at fixture level, never silently pass. A test that can pass with zero real work is a vacuous test and will be rejected at the seal review.

## 3. House test idioms (copy these shapes)

- Naming: `test_<what>_<condition>_<expected>`; invariant-named tests for seals (e.g. `test_verdict_line_is_absent_when_run_aborts_before_evaluation`).
- AAA with blank lines between Arrange / Act / Assert; one assertion focus per test.
- Positive-control pairing: every negative assertion ("sees 0 rows", "refuses") is paired with a positive control in the same test or an adjacent one ("the authorized persona sees exactly 1"), so the test cannot pass on an empty database or a missing feature. See `tests/ask/tools/test_sql_hatch_isolation.py:79-81` for the canonical pair.
- Factory fixtures over cross-module imports: seed helpers are exposed as fixtures returning callables (`tests/ask/conftest.py:246-276`), never imported from another test module.
- Frozen dataclasses for case tables with provenance fields (`tests/ask/security/corpus_types.py`).
- Module docstring: the 4-section header (Role / Used by / Depends on / Key invariants) for any module that needs a live database or pins a rule; one-liners for package markers.
- Subprocess tests decode with `encoding="utf-8"` explicitly. The runner's stdout contains Cyrillic by contract.
- Reserved synthetic domains for any address you mint: `example.test`, `acme.test`, `partner.test`. No real names.

## 4. Binary fixture builders you may reuse

`backend/tests/connectors/extraction/conftest.py` is a plain module of in-memory builders with no pytest fixtures: `build_pdf(page_streams, with_image=False)`, `build_docx(blocks)`, `build_xlsx(...)`, `build_tnef(...)`, `encrypt_pdf(pdf_bytes, user_password, owner_password=None)`, `text_page_stream(...)`. You may import these builders (they are builders, not fixtures) or write your own; do not modify that file.

The extractor entry point the FID contract clauses refer to is `app.connectors.imap.parsing.attachment_extractor.extract_text(attachment: ParsedAttachment) -> ExtractionResult`; `ParsedAttachment` is in `app.connectors.imap.parsing.models`. The date parser TIME refers to is `app.connectors.imap.parsing.headers.parse_date(value: str | None) -> datetime | None`. The redactor RED refers to is `app.connectors.extraction.redact.redact_secrets(text: str) -> tuple[str, int]`. The email ingest service IDEM refers to is `app.connectors.imap.services.email_ingest_service.EmailIngestService` (constructed with a session; `ingest_email(raw_bytes, internal_date=None) -> IngestOutcome`). The reader plane VIS refers to is `app.core.database.reader_session(org_id, person_id)`; the write plane is `app.core.database.scoped_session(org_id)`. You may use these existing public surfaces ONLY to ARRANGE state or to obtain ACTUAL results. EXPECTED values (expected timestamps, redactions, fidelity units, identities, permissions) must come from independently specified fixtures and frozen rules (the RFC, the criterion text, the synthetic original) — never from running the measured component, because that would bless exactly the defects the exam measures (contract R12). You may not assume anything about the instrument's internals.

## 5. The expected-failure rule for a greenfield package

`backend/tools/mem01_verify/` holds only the `release/` data folder today; no instrument module exists. A test that imports `tools.mem01_verify.<module>` at module level would fail at COLLECTION (an ImportError), which the Bible forbids because a collection error hides whether the test itself is sound. Therefore: import the instrument INSIDE the test function (or in a function-scoped fixture), so that today every test fails with `ModuleNotFoundError: No module named 'tools.mem01_verify.<module>'` as an ordinary test failure, and your expected-failure map lists exactly that reason per test. That reason proves only that the test reached its import; it does NOT prove the assertion is sound. So, additionally: (a) deliver a MISSING-SURFACE REPORT — per test, the public surfaces (module.name) it needs, so the orchestrator can confirm the oracle targets exactly the contract's §1.3; (b) keep every fixture and helper of your own runnable and proven WITHOUT the instrument (test your helpers where they contain logic: canonical-hash helpers, synthetic gold/hidden-root builders, `.eml` builders); (c) after the implementation is green, the orchestrator mutation-checks a declared sample of your seals (contract §14.2) — write invariant-named tests whose assertion would visibly go red under a one-clause behavioural revert. When a module lands, the failure reason moves to the real assertion. Tests of pure surfaces (verdict grammar, statuses, hashing, evid_norm, leakage.group_rows, classify_content_language, criteria loading, result block) need no database and must run in well under a second each. Database-backed tests skip loudly only when the server is unreachable; Stage A is not "done" with any required test skipped.

## 6. Hard rules for the author (from the Bible, Phase 3)

1. Touch nothing outside `backend/tests/tools/` (plus `backend/pyproject.toml`/`uv.lock` ONLY for a test-infra dependency, e.g. a property-testing library — say so in the report).
2. Every test fails today for the expected reason; deliver the expected-failure map proven by a real run (`uv run pytest tests/tools -p no:cacheprovider -q`).
3. Anti-vacuity: no test passes by accident against the missing instrument; negatives carry positive controls.
4. Cover the contract AND the edges it implies: exact strings and separators, boundaries (20th vs 21st hidden unit; denominator == minimum; empty quote; a quote that normalizes to empty; ubiquity cap at exactly 25 vs 26 carriers; combining marks; CRLF), ordering (UTF-8 self-test before anything; charge before evaluation; attempt written before execution), zero-denominator policies, aborted-vs-completed runs, no personal data on stdout, read-only refusal of writes to the configured database, probe database dropped after use.
5. List the underdetermined cases NOT written, with the clause that leaves them open.
6. `uv run ruff check tests/tools` clean. No commits.
