# PR-6 (PC-06) adversarial review — GDPR erasure + compliance export

| Field | Value |
|---|---|
| **PR / Epic** | PR-6 / [PC-06](../PM/platform-console/EPIC-PC-06-erasure.md) |
| **Commit reviewed** | `8e9c531` (backend) |
| **Method** | Multi-agent `Workflow` — 5 lenses (PII-completeness, legal-hold/atomicity, authz/content, code-quality, tests) → refute-by-default verification of every finding |
| **Scale** | 20 agents · ~1.29M subagent tokens · 289 tool calls |
| **Outcome** | **4 confirmed (1 high, 2 medium, 1 low), 7 dismissed.** All confirmed fixed in the follow-up commit. |
| **Date** | 2026-06-01 |

## Confirmed findings & fixes

| # | Sev | Finding | Fix |
|---|---|---|---|
| 1 | **High** | **Legal-hold guard is a lock-free TOCTOU.** `erase_organization` read `legal_hold` via a plain `SELECT` (`get_by_id`) and committed the deletes only at end-of-request; under READ COMMITTED a concurrent `set_legal_hold(True)` could commit in the window → **data destroyed under a hold now in force**. The module already defends this exact class with `FOR UPDATE` (PC-05 transitions, DYN-01 last-admin) — the *most destructive* path was the one that omitted it. Raised by 2 lenses. | Added `OrganizationRepository.get_for_update` (`SELECT … FOR UPDATE`); erase now loads the org locked **before** the legal-hold check, so a concurrent hold-set blocks until the erase commits (or is ordered strictly before the erase's locked read → 409). The `legal_hold` read is now atomic with the deletes. |
| 2 | Med | The legal-hold test asserted only 1 of ~5 "touch nothing" facets (user count). | Strengthened: seeds a refresh token + a support_grant with a non-null decider email, then after the 409 asserts tokens intact, `decided_by_email` intact, status still `active`, and **no `org.erased`** audit row. |
| 3 | Med | Missing destructive-path cases: unknown-org 404, re-erase idempotency, export-after-erase, 422 validation. | Added all four (`test_erase_unknown_org_returns_404`, `…_export_unknown_org_returns_404`, `test_re_erase_is_idempotent`, `test_compliance_export_after_erase_still_builds`, `test_erase_missing_reason_returns_422`). |
| 4 | Low | Token deletion verified only via the self-reported certificate count, never against the table. | Both legal-hold and full-erase tests now assert `refresh_tokens` directly (intact vs zero). |

> *Concurrency-test note:* a deterministic end-state test of the erase-vs-`set_legal_hold` race isn't feasible (the two correct orderings — hold-wins-409 and erase-wins-then-hold-applies — share the same end state). The fix is correct-by-construction (the proven `FOR UPDATE` convention used by PC-05/DYN-01) and the sequential legal-hold guard is fully covered.

## Dismissed (verifier-rejected — all correctly)

- **`audit_log.ip_address` left behind** — retained, but the certificate discloses retention at **table scope** ("the append-only audit_log is retained"), so ip_address is within the disclosed set; certificate honest. (Added ip_address to the doc PII maps as a nit.)
- **`support_grant.decided_by_user_id` retained** — a bare pseudonymous UUID with the email scrubbed; parity with the deliberately-retained `audit_log.actor_id`. Settled design.
- **Export truncation at 1000 / atomicity-test / "full" wording** — the cap is documented + tracked (streamed export deferred); an atomicity test would assert framework behavior (forbidden by the testing rules); the all-or-nothing property is framework-guaranteed by the `get_session` unit-of-work.

## Post-fix state

- Full backend suite **183 passed** (+5 review-fix tests), ruff clean, coverage 92.8%.
- The TOCTOU fix mirrors the proven `FOR UPDATE` pattern already in `support_grant` + the last-admin guard.
- Fixes committed on `feat/platform-erasure` after `8e9c531`.

## Carried forward (post-review change)

- **Sudo password re-auth on erase (`13da7fe`, landed after this review).** Erase now requires a `password`
  re-auth verified before any delete (order: lock → slug 400 → **password 403** → legal-hold 409 → deletes).
  This was **not** part of the reviewed commit. The dynamic adversarial pass
  (`docs/audits/2026-06-01_erasure-dynamic-adversarial.md`) confirmed it live and its side effect: a forged
  dev-secret token (random `sub`) now hits **403** at the password step, so a forged token *alone* can no
  longer erase (TC-ER-032) — the one forged-token write that's now second-factored (suspend/legal-hold/
  support-approve and all reads remain forgeable; see `FIX_BEFORE_PROD` → *Rotate JWT_SECRET*). Recommend the
  control be documented in the epic (done: PC-06-AC5b) and the negative path kept under test.
