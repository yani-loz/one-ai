# TC-IM-E01 — Live RLS isolation on the entity graph (`person` / `person_email`)

| Field | Value |
|---|---|
| **ID** | TC-IM-E01 · **Suite** E (Persistence/RLS/entities) · **Type** Adversarial · **Mode** db-rls |
| **Result** | ✅ **Pass** · **Tag** ✔ CONFIRMS-FIXED · **Severity if fail** Critical (cross-tenant PII leak) · **Status** Executed |
| **Harness** | `harness/rls_entity_isolation.py` |

## Objective
Prove that PostgreSQL Row-Level Security **actually blocks cross-tenant access at the database** on
the entity-graph tables (`person`, `person_email`) — not just that the policy exists in the catalog.

## Break hypothesis
Migration `0009` enabled + FORCE'd RLS on all 12 tenant tables and split the runtime role
(`oneai_app` = NOBYPASSRLS). But the standing-invariant test (`test_rls_invariants.py`) **live-proves
isolation only on `users`**, and every entity/email functional test runs on the **BYPASSRLS** global
engine — so DB-level isolation on the densest-PII tables is *catalog-proven, not row-proven*. If a
policy were mis-scoped, mis-applied, or the app role inadvertently had BYPASSRLS, a cross-tenant
`SELECT` as `oneai_app` would return another org's people. **Predicted (plain reading): RLS holds →
zero cross-tenant rows.** A leak here is Critical.

## Preconditions
Stack up; migration `0009` applied; runtime roles provisioned. Verified live:
`oneai_app super=f bypassrls=f` · `oneai_global super=f bypassrls=t` · `oneai super=t bypassrls=t`.

## Steps (the harness)
1. Seed two **run-stamped throwaway orgs** (A, B), each one `person` + one `person_email`, via the
   **owner** engine (bypass). The two `person_email` rows share the **same address** (`shared-…@…`) —
   legal only because `uq_person_email_identity` is `(org_id, email)`, so this also probes per-org isolation.
2. As **`oneai_app`** with `app.current_org_id = A` (transaction-local) → SELECT the tagged persons.
3. Same with GUC = B.
4. On a **fresh** `oneai_app` connection that never set the GUC → SELECT (fail-closed path).
5. As **`oneai_global`** (BYPASSRLS) → SELECT (the teeth: rows must be visible to *something*).
6. `person_email` SELECT under GUC = A for the shared address.
7. Cross-org **INSERT** (`org_id=B` while GUC=A) and **UPDATE** (`SET org_id=B`) as `oneai_app`.
8. Cleanup: owner `DELETE FROM person WHERE display_name LIKE '% <tag>'` (cascades `person_email`).

## Expected
2→only A; 3→only B; 4→∅; 5→{A,B}; 6→1 row (org A); 7→both rejected with a row-level-security error.

## Execution result (2026-06-09)
**All 7/7 checks passed — RLS holds on the entity graph.** Raw evidence:
```
seeded ORG_A=ca28839f-0e70-40fb-9c20-d77340d68696 ORG_B=d7b556e8-8a7f-4619-84c1-480c42cdf3bc (tag rls-e01-fa53e4ac41bf)
  [PASS] app_scoped_A_sees_only_A          :: visible={'ca28839f-…'}
  [PASS] app_scoped_B_sees_only_B          :: visible={'d7b556e8-…'}
  [PASS] app_fresh_unset_guc_sees_nothing  :: visible=set()
  [PASS] global_bypassrls_sees_both        :: visible={'d7b556e8-…','ca28839f-…'}
  [PASS] person_email_per_org_isolation    :: rows=1 orgs=['ca28839f-…']
  [PASS] app_cross_org_insert_rejected     :: InsufficientPrivilegeError: new row violates row-level security policy for table "person"
  [PASS] app_cross_org_update_rejected     :: InsufficientPrivilegeError: new row violates row-level security policy for table "person"
cleanup: deleted 2 person rows (+cascade)
RESULT: 7/7 checks passed
VERDICT: RLS HOLDS on the entity graph
```

**Verdict:** ✅ Pass — the hardest rule holds **at the DB**, not just the app layer, on the entity
graph. `oneai_app` cannot read, insert, or move a row across tenants; the **teeth** (global role sees
both rows) prove the green is not vacuous — the rows exist and it is RLS, not absence, that hides them.
The shared-address case confirms `person_email`'s per-org match key (`UNIQUE(org_id, email)`) isolates
correctly: the same person's email in two tenants is two independent rows, each invisible to the other.

**Tag — ✔ CONFIRMS-FIXED:** empirically proves the `0009_enforce_rls` flip (`docs/FIX_BEFORE_PROD.md`,
"Enforce RLS" item, marked `[~] DONE`) holds **live on the entity graph**, closing the gap that the
standing-invariant test left (it proves the same only on `users`). This is the single highest-value
positive of the pass — a cross-tenant DB leak is the contract-ending failure, and it does not occur.

## Honest nuances recorded
- **Empty-GUC vs unset-GUC.** A connection that set a transaction-local GUC and let it revert leaves
  the placeholder at `''` (empty string), so the policy's `''::uuid` **errors** rather than returning
  `[]`. Still fail-closed (no leak — the query errors), and the real app sets the GUC on *every*
  transaction via the `after_begin` listener, so it never relies on the unset path. The fail-closed
  zero-rows behaviour is proven on a *fresh* connection (check 3).
- **Scope.** This case covers `person`/`person_email`. The email Layer-1 tables
  (`email_message`/`email_recipient`/`email_attachment`) get the same live proof in **TC-IM-E02**
  (they need a seeded `connector_connection` for the FK chain).
