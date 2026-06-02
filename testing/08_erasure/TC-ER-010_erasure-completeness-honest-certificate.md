# TC-ER-010: Erasure completeness + honest deletion certificate (AC2)

| Field | Value |
|---|---|
| **ID** | TC-ER-010 · **Target** Erasure (PC-06) · **Suite** ERASE |
| **Type** | Positive · **Severity if fail** High · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-FIXED |

## Objective
Erasing a tenant returns a complete + **honest** certificate: it reports what was deleted (users, tokens)
*and* what was lawfully retained (the append-only audit_log), never claiming total erasure.

## Steps / Harness
`provision_company` → `erase_org(E, confirm_slug=E.slug)` (helper sends the sudo `password`). `harness/tc_010.py`.

## Execution result
- **Run at:** 2026-06-01 · **Result:** ✅ Pass · **Tag:** CONFIRMS-FIXED

**Evidence**
```
ERASE 200 | users_erased=1 tokens_deleted=1 status=offboarded audit_log_retained=true
retained_legal_basis="GDPR Art. 17(3) ..." → complete=True honest=True
```

**Verdict**
Defense held. `ErasureService.erase_organization` returns `ErasureCertificateResponse` with truthful
erased-vs-retained counts (`erasure_service.py:121-138`). PC-06-AC2/AC4 confirmed live.
