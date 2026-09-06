# Security Standards

## Tenant Isolation (5 Layers) — HARDEST RULE

1. **PostgreSQL:** `org_id` on every table with NOT NULL constraint
2. **Row-Level Security (RLS):** ENFORCED everywhere including dev since migration 0009 — 22 tables with ENABLE + FORCE and an `org_isolation` policy, on a four-role split (`oneai` owner / `oneai_app` write, no bypass / `oneai_global` platform, BYPASSRLS / `oneai_reader` SELECT-only).
3. **Application-level:** every query scoped to tenant context
4. **API Gateway:** tenant context from JWT — no switching
5. **Within-tenant (PF-01):** per-person visibility via `acl_grant` + the `app.current_person_id` GUC, enforced by RESTRICTIVE SELECT policies on the three content tables for `oneai_reader` only (migration 0019). Migration 0023 extends this to BCC recipients and the person seen-window — written, not yet applied.

*Layer counts, table counts and role names above were measured 2026-09-06 (`docs/audits/2026-09-06_built-vs-docs-map.md` §3). Migration `0023_reader_bcc_and_seen_window.py` is untracked in git and unapplied on the dev database (`alembic_version = 0022_counterparty_summary_v3`) — treat layer 5's BCC and seen-window clauses as unenforced until it is applied.*

**No code path exists that queries without tenant scope. Non-negotiable.**

## Credential Management

- All secrets via `.env` (local) or Docker secrets (production). NEVER in code.
- `.env` in `.gitignore`. `.env.example` committed with placeholders.
- Secrets NEVER in logs, error messages, or docker inspect.
- Each service accesses only the secrets it needs.

## Authentication (JWT) — shipped

- Self-hosted JWT via Platform Service
- Payload: `user_id`, `tenant_id`, `roles`, `expiration`
- Access token: 15 min. Refresh token: 7 days.
- JWT signing key rotatable.
- Bcrypt for password hashing.

## LLM Data Privacy

- No tenant data sent to LLM providers in a way that allows training.
- Use zero data retention endpoints where available.
- NON-NEGOTIABLE.

## Input/Output Security

- Input validation on all endpoints (Pydantic).
- Prompt injection defense in Agent Runtime.
- Output validation: prevent unauthorized data exposure, cross-tenant leakage.
- Sensitive data classification and PII detection.

### Generated-SQL hatch

The Ask layer lets a model-generated SQL string reach the database. That is the one path where prompt text becomes a query, so it carries its own obligations:

- **Static guard first.** `validate_generated_sql` in `backend/app/ask/tools/sql_guard.py` lexes and rejects the statement before it is sent.
- **Plan gate second.** `_assert_plan_is_safe` in `backend/app/ask/tools/sql_execution.py` inspects the query plan (relations touched, row estimates) before `execute_guarded_sql` runs the statement.
- **One plane only.** The session is caller-provided, and the module contract says it MUST come from `core.database.reader_session` — the person-bound, SELECT-only plane (`app/ask/__init__.py:4,12`, `app/ask/services/agent_runner.py:11`). Nothing in `backend/app/ask/` imports `scoped_session`, `get_session` or `GlobalSessionLocal`, and nothing may start: routing a generated statement through the write or BYPASSRLS plane defeats layers 2 and 5 at once.
- **Disclosures are tracked, not folklore.** Every known hole and its status lives in `docs/PM/ask/ASK-SECURITY-LEDGER.md`. V9/V11/V12 are closed *in code only* by migration 0023, which is untracked in git and unapplied on the dev DB — measured 2026-09-06 (`docs/audits/2026-09-06_built-vs-docs-map.md` §3) as 59 readable BCC rows and 5,893 enumerable `acl_grant` rows.
- **Three gates prove it.** `backend/scripts/ask_loop/conformance.py` (behavioural), `seal_check.py` (outcome seals), `defence_matrix.py` (which mechanism stops which attack). Run them by hand after any change to the guard, the plan gate or the reader policies. **They are NOT enforced by CI as of 2026-09-06:** the workflow steps at `.github/workflows/ci.yml:59,62,69` are an uncommitted working-tree edit (absent from `git show HEAD:.github/workflows/ci.yml`), and the trigger is `push: [main]` + `pull_request`.

## Audit Trail

- `audit_log` table is append-only (no UPDATE/DELETE).
- Logs: who, what, when, which entity, details (JSONB), IP address.

## Encryption

- At rest: AES-256 (PostgreSQL encryption)
- In transit: TLS 1.3 (production deployment)
