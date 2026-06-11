# TC-IM-E07 — Org GUC must not leak across pooled transactions

| ID · Suite · Type · Mode |
|---|
| TC-IM-E07 · E (Persistence/RLS/entity graph) · Concurrency · db-rls |

| Result · Tag · Severity · Status |
|---|
| ✅ Pass · — · — · Executed |

## Objective
Confirm tenant isolation is transaction-local: `set_config('app.current_org_id', <org>, is_local=true)`
(re-applied per transaction by the `after_begin` listener, `database.py:90-102`) must never let a
pooled connection reused for org B carry org A's GUC.

## Break hypothesis
The local GUC survives a transaction/commit or a connection checkin, so a connection reused across
orgs leaks the prior org's rows — a cross-tenant exposure through pool reuse.

## Steps (as the real `oneai_app` NOBYPASSRLS role)
1. **Sequential reuse, one connection:** an A-scoped txn (sees only A), then a B-scoped txn on the
   SAME physical connection (must see only B).
2. **Post-txn bleed:** after the committed B-txn, a NON-transactional read on the same connection
   must surface ZERO of any prior org's rows.
3. **Interleaved concurrency:** two connections, two orgs, transactions overlapping via `asyncio`
   (each worker yields mid-transaction) — each must see strictly its own org throughout.

## Expected
No GUC carry-over; the post-txn read is fail-closed; interleaved workers each see only their own org.

## Execution result (2026-06-09)
Harness: `testing/10_imap-connector/harness/rls_guc_concurrency.py`

```
seeded ORG_A=d54809eb-...-658077d6c418 ORG_B=56e110ef-...-8979d730f675 (tag rls-e07-e44bb8f5cd61)
  [PASS] sequential_same_conn_no_guc_carryover :: A-txn saw={ORG_A}, then B-txn saw={ORG_B} on the SAME connection
  [PASS] post_txn_no_cross_org_bleed_fail_closed :: unscoped read after committed B-txn saw=set() via errored (InvalidTextRepresentationError: invalid input syntax for type uuid: "") (LEAK only if A/B rows appear)
  [PASS] interleaved_two_orgs_each_sees_only_own :: A-worker saw=[{ORG_A}], B-worker saw=[{ORG_B}] (each strictly its own org)
cleanup: deleted 2 person rows

RESULT: 3/3 checks passed
VERDICT: GUC is transaction-local; no cross-txn leak
```

**Verdict:** ✅ **Pass** — the GUC is strictly transaction-local. A single physical connection reused
A→B carries no state across; interleaved concurrent transactions for two orgs each see only their own
rows. **Noted nuance (not a defect):** the post-txn unscoped read does not return empty — it
*errors* on `''::uuid`, because a committed `is_local` GUC leaves an empty string rather than NULL.
This is still fail-closed (the query raises rather than leaking prior-org rows), and is the exact
behavior the E01 harness header documents; the real app always re-sets the GUC per transaction via
`after_begin`, so it never relies on the unset path. **Tag:** — (positive concurrency contract).
