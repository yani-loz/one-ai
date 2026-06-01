# EPIC PC-06 — GDPR erasure (legal-hold-beats-erasure) + exportable compliance artifacts

| Field | Value |
|---|---|
| **Epic ID** | PC-06 |
| **Module** | Platform Console (`PC`) |
| **Status** | 🟢 Backend done (PC-06a) — org-level erasure + compliance export |
| **Branch** | `feat/platform-erasure` (off `main`, which now has PC-01…PC-05) |
| **PR** | PR-6 (backend: erasure + certificate + compliance export) |
| **Depends on** | PC-03a (`legal_hold`, `offboarded`), PC-04 (`audit_log` — the retained trail), PC-05 (`support_grant` PII) |
| **Closes (FIX_BEFORE_PROD)** | "Implement GDPR data export + delete" (org-level) |
| **Review** | [docs/audits/2026-06-01_platform-erasure-pr6-review.md](../../audits/2026-06-01_platform-erasure-pr6-review.md) |
| **Date** | 2026-06-01 |

## 1. Goal & context

Make the GDPR right-to-erasure real for **offboarding a tenant** — and make it **provable**.
A platform admin can erase a company's personal data, but a **legal hold overrides erasure**
(litigation/regulatory preservation beats the right to be forgotten). The operation is
**atomic**, **audited**, and yields a **deletion certificate** + an **exportable compliance
artifact** (metadata + the audit trail) — the proof a DACH customer/regulator asks for.

> **The hard truth this PR confronts:** erasure collides with the PC-04 **append-only**
> `audit_log` (its immutability trigger forbids deletion). So erasure **scrubs** the mutable
> stores and **retains** the audit trail under a documented legal basis (Art. 17(3)). The
> certificate is **honest** about erased-vs-retained — it never claims total erasure.

## 2. Scope

**In scope (PC-06a — backend, org-level):**
- `POST /platform/orgs/{id}/erase` — platform-gated; body carries a **reason** + **slug
  confirmation** (GitHub-style guard). **Legal-hold-beats-erasure** (409, nothing touched).
  Atomic: delete `users` + `refresh_tokens` (tokens first — they key on the users' ids),
  **scrub** `support_grant.decided_by_email`, set org `status='offboarded'`, emit `org.erased`.
  Returns the deletion certificate.
- `GET /platform/orgs/{id}/compliance-export` — metadata + the org's full audit trail.

**Out of scope (later):** per-**USER** right-to-erasure (this is org/offboarding-level); the
memory/content layers (Connect/Ask/Learn) joining the erasure path when they exist;
`audit_log.actor_email` pseudonymization; a signed/streamed export for very large trails; a
frontend "Erase / Export" control (a thin follow-up on the org detail screen).

## 3. User stories

| ID | Story |
|---|---|
| PC-06-S1 | As a platform admin, I can **erase** an offboarded tenant's personal data and get a **certificate** of what was erased. |
| PC-06-S2 | As a compliance officer, **legal hold beats erasure** — a held org cannot be erased until the hold is cleared. |
| PC-06-S3 | As a compliance officer, I can **export** an org's metadata + audit trail as proof of processing. |
| PC-06-S4 | As the system, erasure is **atomic + honest** — partial erasure can't be left behind, and the certificate states what was lawfully **retained**. |

## 4. Acceptance criteria → tests (traceability matrix)

> ⭐ = compliance/security-critical. BE = `backend/tests/identity/routes/test_erasure_routes.py`.

| AC | Criterion | Proven by |
|---|---|---|
| ✅ ⭐ PC-06-AC1 | **Legal-hold-beats-erasure:** a held org → **409**, nothing deleted. | `::test_legal_hold_blocks_erasure_and_deletes_nothing` |
| ✅ PC-06-AC2 | Erasure deletes the org's **users + refresh tokens**, sets `offboarded`, and returns a certificate with counts. | `::test_erase_deletes_users_revokes_tokens_offboards_and_certifies` |
| ✅ ⭐ PC-06-AC3 | Erasure **scrubs** `support_grant.decided_by_email` (tenant subject) but **keeps** `requested_by_email` (Ethera staff). | `::test_erase_scrubs_support_grant_decider_email` |
| ✅ ⭐ PC-06-AC4 | The append-only `audit_log` is **RETAINED** (not deleted); the erasure itself is logged (`org.erased`). | `::test_erase_retains_audit_log_and_records_the_erasure` |
| ✅ PC-06-AC5 | A **slug-confirmation** mismatch → **400**, nothing deleted (accidental-destruction guard). | `::test_slug_confirmation_mismatch_returns_400_and_deletes_nothing` |
| ✅ ⭐ PC-06-AC6 | Both endpoints reject a **company token** (401 — platform-only). | `::test_erase_rejects_company_token`, `::test_compliance_export_rejects_company_token` |
| ✅ PC-06-AC7 | The compliance export returns **metadata + the audit trail**. | `::test_compliance_export_returns_metadata_and_trail` |
| ⏳ PC-06-AC8 | Frontend: an "Erase / Export" control on the org detail screen. | **PC-06b — not yet built.** |

## 5. Implementation map

| Area | Files |
|---|---|
| Service | `services/erasure_service.py` (legal-hold-first, atomic, honest certificate, export) |
| Data access | `repositories/{user,refresh_token,support_grant}_repository.py` (the erasure deletes/scrub) |
| API | `routes/erasure_routes.py`, `schemas/erasure_schemas.py`, `dependencies.py`, `router.py` |
| Errors | `exceptions.py` (`LegalHoldError`→409, `ErasureConfirmationError`→400) + `error_handlers.py` |
| Audit | `services/audit_service.py` (`org.erased`) |

## 6. Decisions settled during the PR

- **Tenant-PII completeness pass (the advisor's blocking catch):** the premise "audit never
  stored emails" was **false** — `audit_log.actor_email` (auth events) + `support_grant.
  decided_by_email` also hold tenant PII. Erasure was widened to all four stores with an
  explicit **scrub-vs-retain** decision each, and a **standing invariant** added
  (`FIX_BEFORE_PROD`): every new tenant-scoped table must be wired into the erasure path.
- **Append-only vs erasure:** `audit_log` can't be deleted (immutability trigger), so it is
  **retained under documented legal basis** (Art. 17(3)); `actor_email` pseudonymization is the
  tracked real fix. The certificate reports retained-vs-erased honestly.
- **Atomic + legal-hold-first:** confirm slug (400) → legal-hold (409, touch nothing) → delete
  tokens (before users) → scrub → delete users → offboard → audit, all in one transaction.
- **Org row retained** at `offboarded` as the subject of the compliance record; B2B org
  name/slug kept (not personal data).

## 7. Remaining + notes

- **PC-06b (frontend):** an "Erase company / Export compliance" control on the org detail
  screen (with the slug-confirmation prompt) — a thin follow-up.
- **Per-user erasure**, content-layer erasure, `actor_email` pseudonymization, signed export →
  tracked in `FIX_BEFORE_PROD`.
- **Dynamic QA (Target 08):** an adversarial live pass over erasure (legal-hold bypass attempts,
  partial-failure atomicity, PII-left-behind sweep) to follow.
