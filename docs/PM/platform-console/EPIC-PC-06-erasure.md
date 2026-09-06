# EPIC PC-06 — GDPR erasure (legal-hold-beats-erasure) + exportable compliance artifacts

| Field | Value |
|---|---|
| **Epic ID** | PC-06 |
| **Module** | Platform Console (`PC`) |
| **Status** | ✅ **Done** — org-level erasure + compliance export (PC-06a) + the "Erase / Export" UI (PC-06b) |
| **Branch / commit** | `main` · `8e9c531` (PC-06a backend) + `7b750f3` (review fixes) + `81a8655` (PC-06b UI), all 2026-06-01; also on `feat/platform-erasure` |
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
  confirmation** (GitHub-style guard) + a **sudo-style `password` re-auth** (added in `13da7fe`:
  the admin re-enters their own password, verified before any delete; wrong/absent → 403/422,
  nothing touched). **Legal-hold-beats-erasure** (409, nothing touched). Guard order:
  lock (FOR UPDATE) → slug (400) → **password (403)** → legal-hold (409) → deletes. Atomic:
  delete `users` + `refresh_tokens` (tokens first — they key on the users' ids), **scrub**
  `support_grant.decided_by_email`, set org `status='offboarded'`, emit `org.erased`. Returns
  the deletion certificate.
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
| ✅ ⭐ PC-06-AC1 | **Legal-hold-beats-erasure:** a held org → **409**, nothing deleted (users, tokens, decider-email, status, audit all untouched). | `::test_legal_hold_blocks_erasure_and_deletes_nothing` |
| ✅ ⭐ PC-06-AC1b | **Race-safe guard:** erase loads the org `FOR UPDATE`, so a concurrent `set_legal_hold` can't be overwritten by an in-flight erase (TOCTOU closed). | Correct-by-construction (the `FOR UPDATE` convention; review fix) — deterministic end-state test infeasible (orderings share an end state) |
| ✅ PC-06-AC2 | Erasure deletes the org's **users + refresh tokens**, sets `offboarded`, and returns a certificate with counts. | `::test_erase_deletes_users_revokes_tokens_offboards_and_certifies` |
| ✅ ⭐ PC-06-AC3 | Erasure **scrubs** `support_grant.decided_by_email` (tenant subject) but **keeps** `requested_by_email` (Ethera staff). | `::test_erase_scrubs_support_grant_decider_email` |
| ✅ ⭐ PC-06-AC4 | The append-only `audit_log` is **RETAINED** (not deleted); the erasure itself is logged (`org.erased`). | `::test_erase_retains_audit_log_and_records_the_erasure` |
| ✅ PC-06-AC5 | A **slug-confirmation** mismatch → **400**, nothing deleted (accidental-destruction guard). | `::test_slug_confirmation_mismatch_returns_400_and_deletes_nothing` |
| ✅ ⭐ PC-06-AC5b | **Sudo password re-auth** (`13da7fe`): a wrong password → **403**, an absent one → **422**, nothing deleted; the password is verified before any delete. **Side effect (dynamic, TC-ER-032):** a forged dev-secret token with a random `sub` resolves to no admin → **403**, so a forged token alone can no longer erase — partially mitigating the `Rotate JWT_SECRET` deferral *for the erase endpoint only*. | BE `::test_erase_wrong_password_returns_403` ; dynamic `docs/audits/2026-06-01_erasure-dynamic-adversarial.md` (TC-ER-032/023) |
| ✅ ⭐ PC-06-AC6 | Both endpoints reject a **company token** (401 — platform-only). | `::test_erase_rejects_company_token`, `::test_compliance_export_rejects_company_token` |
| ✅ PC-06-AC7 | The compliance export returns **metadata + the audit trail**. | `::test_compliance_export_returns_metadata_and_trail` |
| ✅ PC-06-AC8 | Frontend: an "Erasure & compliance" control on the org detail screen — export + a type-the-slug erase confirmation. | FE `OrganizationDetailPage.test.tsx::test_renders_the_erasure_panel`, `::test_erase_requires_slug_confirmation_then_offboards` (`OrgErasurePanel.tsx`) |

## 5. Implementation map

| Area | Files |
|---|---|
| Service | `services/erasure_service.py` (legal-hold-first, atomic, honest certificate, export) |
| Data access | `repositories/{user,refresh_token,support_grant}_repository.py` (the erasure deletes/scrub) |
| Content-layer hooks (added after PR-6) | `backend/app/common/erasure_hooks.py` — `REQUIRED_ERASURE_HOOKS`, registered in `create_app`; `erasure_service.py` runs every hook inside the erase transaction and fails closed on a missing one. Shipped as `("connectors", "entities")` in `4808ea5` (2026-06-11); `"access"` added by `db1795d` (2026-07-04, PF-01) |
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
- **Atomic + legal-hold-first + ROW-LOCKED:** load the org `FOR UPDATE` → confirm slug (400) →
  legal-hold (409, touch nothing) → delete tokens (before users) → scrub → delete users →
  offboard → audit, all in one transaction. The lock (added in review) closes a TOCTOU where a
  concurrent `set_legal_hold` could be overwritten by an in-flight erase — the same `FOR UPDATE`
  convention PC-05 + the DYN-01 last-admin guard use.
- **Retained-PII disclosure** is at TABLE scope: the certificate states the whole append-only
  `audit_log` is retained (Art. 17(3)) — covering `actor_email` AND `ip_address`. Pseudonymizing
  both at write time (keep `actor_id`) is the tracked real fix (`FIX_BEFORE_PROD`).
- **Org row retained** at `offboarded` as the subject of the compliance record; B2B org
  name/slug kept (not personal data).

## 7. Remaining + notes

- ✅ **PC-06b (frontend) — DONE:** `OrgErasurePanel.tsx` on the org detail screen — "Export
  compliance record" (downloads the bundle as JSON) + "Erase company" behind a type-the-slug
  + reason confirmation modal (the destructive-action guard), with a legal-hold (409) message
  and an already-erased state. The erase reloads the detail (offboarded) + refreshes the trail.
- ✅ **Content-layer erasure — DONE** (`4808ea5`, 2026-06-11 — the 2026-06-10 audit fix pass): the
  Connect + entity-graph tables now erase through the hook registry in
  `backend/app/common/erasure_hooks.py` (`REQUIRED_ERASURE_HOOKS`), and `ErasureService` **fails
  closed** (`ErasureNotConfiguredError` → 500, nothing deleted) if a required hook is unregistered.
  Shipped as `("connectors", "entities")`; `"access"` was added by `db1795d` (2026-07-04, PF-01).
- **Still open:** per-user erasure, `actor_email`/`ip_address` pseudonymization, signed/streamed
  export → tracked in `FIX_BEFORE_PROD`.
- ✅ **Dynamic QA (Target 08) — DONE:** the adversarial live pass over erasure ran and is recorded in
  [`docs/audits/2026-06-01_erasure-dynamic-adversarial.md`](../../audits/2026-06-01_erasure-dynamic-adversarial.md)
  (legal-hold bypass attempts, partial-failure atomicity, PII-left-behind sweep), with the 16 case
  files under `testing/08_erasure/`. Per the 2026-09-06 inventory
  (`docs/audits/2026-09-06_built-vs-docs-map.md` §8.2, D55) that audit's core security finding is
  still open — read the audit before treating erasure as closed.
