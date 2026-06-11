# TC-IM-E03 — Same display name, different addresses → two distinct persons

| ID · Suite · Type · Mode |
|---|
| TC-IM-E03 · E (Persistence/RLS/entity graph) · Positive · ingest |

| Result · Tag · Severity · Status |
|---|
| ✅ Pass · — · — · Executed |

## Objective
Confirm v1 entity resolution matches by NORMALIZED email only — never by display name — so two
people who happen to share a name but use different addresses/domains resolve to TWO distinct persons
(no over-merge that would pollute a dossier).

## Break hypothesis
A name-matching tier sneaks in and merges two distinct humans named "Jordan Doe" into one person.

## Steps
1. Seed a run-stamped org + connection.
2. Ingest two emails: `From: Jordan Doe <jordan-<S>@globex.test>` and
   `From: Jordan Doe <jordan-<S>@initech.test>` (identical name, different address + domain).
3. Count persons with `display_name = 'Jordan Doe'` in the org.

## Expected
Exactly 2 persons (deterministic email-only matching — `entity_resolver._get_or_create_person`
keys on `person_email.email`, not name; there is no name-merge tier in v1, by design §6).

## Execution result (2026-06-09)
Harness: `testing/10_imap-connector/harness/entity_resolution_suite.py` (case E03)

```
  [PASS] e03_same_name_diff_address_two_persons :: persons named 'Jordan Doe' = 2 (expected 2 — no name merge in v1)
```

**Verdict:** ✅ **Pass** — two distinct persons; no name-based over-merge. **Tag:** — (positive
contract). The design's "over-exclusion under-creates, never over-merges" claim holds here.
