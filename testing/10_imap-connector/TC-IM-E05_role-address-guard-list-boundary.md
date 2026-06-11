# TC-IM-E05 — Role-address guard + conservative-list boundary

| ID · Suite · Type · Mode |
|---|
| TC-IM-E05 · E (Persistence/RLS/entity graph) · Boundary · ingest |

| Result · Tag · Severity · Status |
|---|
| ⚠️ Pass-with-concern · — · Info · Executed |

## Objective
Confirm `is_role_address` (`address_rules.py:21-41, 64-73`): role/shared mailboxes
(`info@`, case-insensitive `iNfO@`, `+suffix` `info+sales@`) never mint a Person. Then probe the
list boundary: role-ish local-parts NOT in `_ROLE_LOCALPARTS` (`enquiries@`, `vertriebsteam@`) DO
mint a Person.

## Break hypothesis
A role address slips the guard and mints a Person (over-creation of a shared mailbox as a human);
or the `+suffix`/case handling is wrong so `info+sales@`/`iNfO@` are treated as distinct people.

## Steps
1. Ingest 3 emails from `info@`, `iNfO@`, `info+sales@` (same `role-<S>.test` domain). Assert 0
   `person_email` rows on that domain.
2. In a fresh org, ingest from `enquiries@firm-<S>.test` and `vertriebsteam@firm-<S>.test`. Assert
   2 `person_email` rows.

## Expected
- info@ / iNfO@ / info+sales@ → 0 persons (✅ — `is_role_address` lowercases and strips the `+suffix`
  for classification: base `info` ∈ `_ROLE_LOCALPARTS`).
- enquiries@ / vertriebsteam@ → 2 persons (⚠️ — neither base is in the list, so they pass the guard
  and mint persons: the conservative-list boundary).

## Execution result (2026-06-09)
Harness: `testing/10_imap-connector/harness/entity_resolution_suite.py` (case E05)

```
  [PASS] e05_role_addresses_mint_no_person :: person_email rows for info@/iNfO@/info+sales@ = 0 (expected 0 — role guard)
  [PASS] e05_unlisted_rolelike_mints_person :: person_email for enquiries@/vertriebsteam@ = 2 (2 = list boundary leak)
```

**Verdict:** ⚠️ **Pass-with-concern.** The role guard works exactly as designed — case-insensitive,
`+suffix`-tolerant. The boundary behavior (`enquiries@`/`vertriebsteam@` minting persons) is the
documented, **intended** under-exclusion: `address_rules.py:12-14` explicitly calls these
"conservative starter lists, intentionally extensible," where over-inclusion only under-creates a
recoverable fragment. This is correct-by-design, not a defect — surfaced only so the list boundary is
on record. **Tag:** — (documented-in-code deliberate trade-off; not a FIX_BEFORE_PROD item).
**Severity: Info.**
