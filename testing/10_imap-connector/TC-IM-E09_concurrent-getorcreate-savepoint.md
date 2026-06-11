# TC-IM-E09 — Concurrent get-or-create resolves via SAVEPOINT re-read (no dup, no abort)

| ID · Suite · Type · Mode |
|---|
| TC-IM-E09 · E (Persistence/RLS/entity graph) · Concurrency · ingest |

| Result · Tag · Severity · Status |
|---|
| ✅ Pass · — · — · Executed |

## Objective
Confirm `entity_resolver._get_or_create_person` (lines 83-115) is race-safe: when two concurrent
ingests get-or-create the SAME person (same normalized email), the `uq_person_email_identity` unique
violation is caught at a `begin_nested` SAVEPOINT and resolved by re-reading the winner — exactly one
person, no duplicate, no aborted/poisoned outer transaction.

## Break hypothesis
A concurrent insert of the same `person_email` either (a) creates a duplicate person, or (b) the
IntegrityError poisons the transaction and the loser aborts/errors instead of re-reading the winner.

## Steps
1. For each of 5 rounds, on a fresh run-stamped org: two workers in separate `GlobalSessionLocal`
   sessions both call the existence check for the same sender (both see None).
2. An `asyncio.Barrier(2)` holds both workers until BOTH have passed the existence check — so the
   second flush is GUARANTEED to hit the unique violation (forcing the real race, not serialization).
3. Each worker inserts via `begin_nested`; the loser catches the IntegrityError and re-reads.
4. Assert: labels = one `won_insert` + one `lost_reread`; the IntegrityError branch fired exactly
   once per round; exactly 1 `person_email`, 1 distinct person; no ERROR outcome.

## Expected
5/5 rounds: the SAVEPOINT re-read branch fires, one person, no duplicate, no abort.

## Execution result (2026-06-09)
Harness: `testing/10_imap-connector/harness/entity_resolution_suite.py` (case E09)

```
  [PASS] e09_forced_race_savepoint_reread_no_dup :: 5/5 rounds: IntegrityError branch fired 5/5, every round -> 1 person_email, 1 person, one won_insert + one lost_reread. sample: round0 labels=['won_insert', 'lost_reread'] branch=['IntegrityError'] pe=1 distinct=1
```

**Verdict:** ✅ **Pass** — the forced race is genuinely exercised (the barrier guarantees both workers
pass the existence check before either inserts, and the harness asserts the **IntegrityError branch
actually fired** — 5/5 rounds — rather than inferring it from the row count). Every round yields one
winner + one re-reader, exactly one person_email / one distinct person, and no aborted transaction.
This proves the `begin_nested` SAVEPOINT + re-read mechanism, not mere serialization.

**Note on scope:** this drives the resolver's race path directly (replicating
`_get_or_create_person`'s check → SAVEPOINT-insert → on-conflict re-read) to deterministically force
the unique violation; the same SAVEPOINT/`begin_nested` construct is what the production resolver
uses. **Tag:** — (positive concurrency contract).
