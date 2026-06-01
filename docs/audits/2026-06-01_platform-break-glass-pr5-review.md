# PR-5 (PC-05) adversarial review — break-glass support-access grant lifecycle

| Field | Value |
|---|---|
| **PR / Epic** | PR-5 / [PC-05](../PM/platform-console/EPIC-PC-05-break-glass.md) |
| **Commit reviewed** | `806a569` (backend) |
| **Method** | Multi-agent `Workflow` — 5 lenses (consent/isolation, state-machine, code-quality, tests, schema) → refute-by-default verification of every finding |
| **Scale** | 16 agents · ~888k subagent tokens · 183 tool calls |
| **Outcome** | **6 confirmed (1 medium-substantive, 1 medium-coverage, 4 low), 3 dismissed.** All fixed in the follow-up commit. |
| **Date** | 2026-06-01 |

## Confirmed findings & fixes

| # | Sev | Finding | Fix |
|---|---|---|---|
| 1 | **Med** | **Lost-update / TOCTOU on transitions.** approve/deny/revoke were read-modify-write with a plain `SELECT` (no `FOR UPDATE`) and an UPDATE keyed on `id` only. Two concurrent privileged actors could both read `requested` and both commit — an **approve racing a revoke leaves the grant `approved`+active for up to 4h despite a logged revoke**. The module already fixes this class (DYN-01 `lock_active_admin_ids` with `with_for_update`). | `SupportGrantRepository.get_in_org` + `get_for_requester` (the transition loaders, never the lists) now `SELECT … FOR UPDATE` → concurrent transitions serialize; the second re-reads the committed status and its guard rejects (409). New test `test_concurrent_transitions_serialize_via_row_lock` (two simultaneous approves → exactly one `ok`, one `rejected`). |
| 2 | Med | `GET /platform/support-requests` (requester-scoped list) had **zero coverage** — a dropped `requested_by_admin_id` filter would leak every admin's requests. | `test_list_my_requests_is_requester_scoped` (two admins → A sees only A's). |
| 3 | Low | No cross-tenant negative test for company `deny`/`revoke` (the per-endpoint rule). | `test_cross_tenant_deny_returns_404`, `test_cross_tenant_revoke_returns_404` (+ approved-then-cross-org-revoke). |
| 4 | Low | Revoke **happy path** untested (both sides). | `test_company_revoke_approved_grant_succeeds` (status→revoked, is_active False, `support.revoked` logged), `test_platform_revoke_own_request_succeeds`. |
| 5 | Low | `reason` validation (422) untested. | `test_request_empty_reason_returns_422`. |
| 6 | Low | `models/__init__` docstring said "four ORM models" (now six). | Reworded count-free ("every identity ORM model"). |

**Bonus (dismissed-but-acted):** the `0006` RLS-policy omission was *dismissed* (tracked + inert), but the verifier flagged the inconsistency with `0003` (which defines its inert policy in-migration). For consistency with that precedent + the standing rule, `0006` now also defines the inert `org_isolation` policy on `support_grant`; the platform-side cross-org flows are noted as needing the BYPASSRLS path (alongside login/onboard) in `FIX_BEFORE_PROD`.

## Dismissed (verifier-rejected)

- **`audit_service` "Used by" docstring not updated** — pre-existing non-exhaustive convention (`UserService`, a prior emitter, was already omitted); no PR regression.
- **Partial illegal-transition matrix** (approve-after-revoke, etc.) — the guards are structurally **flat** (a single value-agnostic comparison / set-membership), and the existing 409 tests already drive those exact branches; extra cases would guard against a code shape that doesn't exist.

## Post-fix state

- Full backend suite **170 passed** (+7 review-fix tests), ruff clean, coverage 93.8%; migration `0006` re-applied (now incl. the inert RLS policy).
- The lost-update fix is verified live by the concurrency test (without `FOR UPDATE` it would return `["ok","ok"]`).
- Fixes committed on `feat/platform-break-glass` after `806a569`.
