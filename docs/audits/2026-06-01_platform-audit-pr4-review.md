# PR-4 (PC-04) adversarial review — append-only `audit_log` + action trail

| Field | Value |
|---|---|
| **PR / Epic** | PR-4 / [PC-04](../PM/platform-console/EPIC-PC-04-audit-log.md) |
| **Commit reviewed** | `944776d` (backend core) |
| **Method** | Multi-agent `Workflow` — 5 review lenses (security, transactions, code-quality, tests, schema) → refute-by-default verification of every finding |
| **Scale** | 11 agents · ~754k subagent tokens · 207 tool calls |
| **Outcome** | **5 confirmed (1 high, 1 medium, 2 low — the high raised by 2 lenses), 1 dismissed.** All confirmed findings fixed in the follow-up commit. |
| **Date** | 2026-06-01 |

## Confirmed findings & fixes

| # | Sev | Finding | Fix |
|---|---|---|---|
| 1 | **High** | **Unbounded inbound `X-Request-ID` overflows `audit_log.request_id` (`VARCHAR(64)`).** On a *success* event the same-transaction INSERT raises `StringDataRightTruncationError` (22001) → SQLAlchemy `DBAPIError` (**not** `DataError`, so `_handle_data_error` doesn't map it → raw 500), and `get_session` rolls back the **whole request — undoing the login/refresh/onboard/suspend itself**. Breaks the documented precondition ("every row field built from validated inputs") that the same-tx coupling's safety rests on — `request_id` was the one unvalidated external field. Raised independently by the **security** and **transactions** lenses. | Clamp at the source: `REQUEST_ID_MAX_LENGTH = 64` in `request_context.py`, `[:64]` on the inbound header; defensive clamp in `AuditService._build_row`; column comment links the width. Regression test `test_oversized_request_id_does_not_roll_back_the_login` (200 + one bounded row). |
| 2 | Medium | **conftest teardown isolation hazard in the partial-schema state.** With `audit_log` (its own later migration 0005) the sole missing table, the all-or-nothing branch took the DROP path and **skipped TRUNCATE**, leaving the other four identity tables un-truncated across tests → order-dependent `slug` collisions for a dev who runs pytest at head 0004 before `alembic upgrade head`. CI unaffected. | Teardown now does **both**: TRUNCATE the pre-existing subset **and** drop the created tables. Docstring updated. |
| 3 | Low | **No test asserts `auth.login.blocked`** — the compliance/incident event (valid creds vs a suspended org) and the only *populated-row* exercise of `record_independently()`. A regression dropping it would stay green. | Added `test_blocked_login_records_event_with_full_actor` (asserts `actor_id`/`org_id`/`actor_email`/`details.reason`). |
| 4 | Low | **`details` typed as bare `dict`** (implicit `Any`) in model + read schema — A4 violation, inconsistent with `AuditEvent.details: Mapping[str, object]`; the only two bare dicts in the app. | Tightened both to `dict[str, object]`. |

## Dismissed (verifier-rejected)

- **Unfiltered `GET /platform/audit` newest-first page not served by an index (seq scan + top-N sort).** Technically accurate but **not an actionable defect in this PR**: zero schema drift, deliberate index design for the *filtered* paths, premature-optimization at current scale, and **already tracked** in `FIX_BEFORE_PROD.md` §audit item (5) (volume/retention + index coverage). No new regression.

## Post-fix state

- Full backend suite **149 passed** (+2 regression tests), ruff clean, coverage 95.6%.
- Live-verified earlier: append-only trigger blocks UPDATE/DELETE as superuser; failed-login independent-writer captures metadata-only rows.
- Fixes committed on `feat/platform-lifecycle` after `944776d`.
