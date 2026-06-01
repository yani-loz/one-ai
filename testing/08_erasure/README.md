# Target 08 — GDPR erasure + compliance export (PC-06) — Adversarial Validation

> Dynamic, adversarial validation of **PC-06 erasure** against the **live stack**: `POST
> /platform/orgs/{id}/erase` (slug-confirmed, legal-hold-gated, atomic) + `GET …/compliance-export`.
> Companion to `docs/audits/2026-06-01_platform-erasure-pr6-review.md` (4 findings fixed incl. the **High**
> legal-hold TOCTOU, 0 remaining defects) and `docs/PM/platform-console/EPIC-PC-06-erasure.md`. Case code
> **`ER`** (`TC-ER-NNN`).

## Scope

**In scope:**
- ⭐ **Legal-hold-beats-erasure:** a held org → **409**, nothing touched (users, tokens, decider-email,
  status, audit). The slug-confirm is checked first (400). Row-locked (`get_for_update`) — the TOCTOU the
  review fixed (a concurrent `set_legal_hold` can't be overwritten by an in-flight erase).
- **Atomicity / completeness:** erase deletes `users` + `refresh_tokens`, **scrubs**
  `support_grant.decided_by_email` (tenant subject) but **keeps** `requested_by_email` (Ethera staff), sets
  `offboarded`, emits `org.erased` — all in one transaction. **PII-left-behind sweep** via psql.
- ⭐ **Append-only audit RETAINED:** the immutable `audit_log` survives erasure (Art. 17(3)); the erasure
  itself is logged. The certificate is **honest** about erased-vs-retained.
- **Slug-confirm guard:** mismatch → **400**, nothing deleted (accidental-destruction guard).
- ⭐ **Audience / forged-token:** both endpoints reject a company token (401); a **forged dev-secret platform
  token erases any org** (catastrophic, irreversible — documented blast radius).
- **Compliance export:** metadata + audit trail; content-blind; builds after erase; unknown org → 404.
- **Idempotency:** re-erase of an already-offboarded org behaves sanely.

**Out of scope:** the frontend "Erase / Export" control (PC-06b); per-USER erasure; content-layer erasure;
`actor_email` pseudonymization (tracked).

## Environment

- Live stack `:8000`; harness inside the backend container, self-contained over stdin:
  `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/<script>.py | docker compose exec -T backend python -`
- psql ground-truth on the **db** container (the PII sweep + "nothing deleted" proofs).
- **HARD RULE — IRREVERSIBLE:** erasure deletes users/tokens + offboards. **Only ever erase your own fresh
  run-stamped orgs.** Never demo/globex/another suite's org.

## Status dashboard

> Result: ✅ pass · ❌ fail (a defect/the win) · ⚠️ pass-with-concern. Tag: 🆕 NEW · ✔ CONFIRMS-FIXED ·
> ✖ REFUTES-FIX · 📋 CONFIRMS-DOCUMENTED · — n/a. Filled during synthesis.

| Suite | Cases | Result spread | NEW | Notes |
|---|---|---|---|---|
| HOLD — legal-hold-beats-erasure + slug guard | _pending_ | | | 409 nothing-touched (psql); 400 wrong-slug; row-lock TOCTOU |
| ERASE — completeness + atomicity + PII sweep | _pending_ | | | users/tokens deleted, decider-email scrubbed, requester-email kept, offboarded |
| RETAIN — audit retained + export | _pending_ | | | append-only survives; org.erased logged; export content-blind |
| AUTHZ — audience + forged-token | _pending_ | | | company token → 401; forged platform token erases any org (documented) |

## Coverage → PC-06 acceptance criteria (filled during synthesis)

| AC | Criterion | Dynamic proof |
|---|---|---|
| ⭐ PC-06-AC1 | legal-hold → 409, nothing deleted | _pending_ |
| ⭐ PC-06-AC1b | race-safe guard (FOR UPDATE) | _pending_ (corroboration) |
| PC-06-AC2 | deletes users + tokens, offboards, certifies | _pending_ |
| ⭐ PC-06-AC3 | scrubs decider email, keeps requester email | _pending_ |
| ⭐ PC-06-AC4 | append-only audit retained; erasure logged | _pending_ |
| PC-06-AC5 | slug mismatch → 400, nothing deleted | _pending_ |
| ⭐ PC-06-AC6 | both endpoints reject a company token (401) | _pending_ |
| PC-06-AC7 | compliance export = metadata + trail | _pending_ |
