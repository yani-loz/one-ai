# TC-IM-C08 — Forged / missing Date → attacker-controlled or NULL received_at

| ID · Suite · Type · Mode |
|---|
| TC-IM-C08 · C (Parse & data quality) · Adversarial · pure |

| Result · Tag · Severity · Status |
|---|
| ⚠️ Pass-with-concern · 🆕 NEW · Low · Executed |

## Objective
Show that on the disk/no-INTERNALDATE path, `received_at` is derived from attacker-controlled headers
(or NULL), so the list ordering (`received_at DESC NULLS LAST`) can be manipulated.

## Break hypothesis
`parse_email` sets `received_at = internal_date or received_at_from_headers(message)`. When there is
no IMAP INTERNALDATE (the disk path) and no `Received` header, `received_at_from_headers` falls back
to the `Date:` header — fully attacker-controlled. A forged **future** Date therefore pins the email
to the top of `EmailMessageRepository.list_for_org` (`order_by received_at.desc().nulls_last()`,
email_repository.py:60). A fully absent date yields NULL → sinks to the bottom.

## Steps
1. Parse an email with `Date: Fri, 31 Dec 2099 23:59:59 +0000`, no Received header, `internal_date=None`.
2. Parse an email with no Date/Received and `internal_date=None`.

## Expected
(1) `received_at.year == 2099` (attacker-controlled top-of-list); (2) `received_at is None`.

## Execution result (2026-06-09)
```
[FAIL] C08_forged_future_date_becomes_received_at :: received_at=2099-12-31 23:59:59+00:00 (attacker-controlled; mis-orders list_for_org received_at DESC NULLS LAST)
[PASS] C08_absent_date_yields_null_received_at :: received_at=None (NULL — sinks to bottom under NULLS LAST)
```

**Verdict:** ⚠️ Pass-with-concern — reproduced. With no INTERNALDATE, `received_at` is taken straight
from the forgeable `Date` header, so a 2099 date pins the email to the top of the org's email list;
an absent date NULLs out and sorts last. The mis-ordering claim is tied to `list_for_org`'s
`received_at.desc().nulls_last()` (read in source, not separately DB-tested). Low severity (ordering
nuisance, no leak); the production fetch path supplies a real INTERNALDATE which neutralizes the
forged-Date vector — the concern is the disk/no-INTERNALDATE fallback.

**Tag:** 🆕 NEW · Severity Low.
