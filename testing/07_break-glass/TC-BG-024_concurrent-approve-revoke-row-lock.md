<!--
  Test-case: TC-BG-024 — concurrent approve vs revoke on one grant (AC4b row lock).
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-024: Concurrent approve + revoke on one grant → no lost update (row lock)

| Field | Value |
|---|---|
| **ID** | TC-BG-024 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | STATE — transition state-machine + concurrent row-lock |
| **Type** | Concurrency |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify PC-05-AC4b — the lost-update fix (audit finding #1). A company **approve** and a company
**revoke** fired concurrently on the **same** grant must serialize on the `SELECT … FOR UPDATE`
row lock so a revoke is **never silently overwritten back to `approved`**. The ordering-independent
invariant: after every concurrent pair, the grant's final status is **`revoked`, never `approved`**.

## Break hypothesis
The attacker's bet (the lost-update / TOCTOU the audit fixed): without `FOR UPDATE`, both
transactions read `requested`, approve writes `approved`+`expires_at`, revoke writes `revoked`
keyed on `id` only — last-writer-wins. If approve commits last, the grant is left **`approved`
and `is_active=true` for up to 4h despite a logged, accepted revoke** — a 200-revoke that did
nothing. A failure: any of the 50 grants ends `approved` (`is_active=true`), or a 500 surfaces
the race as a server error.

NOTE on the result shape — this is the **approve-vs-revoke** race, not the symmetric
two-approves unit test. The two orderings are asymmetric by design:
- **revoke acquires the lock first:** `requested→revoked` (revoke 200); approve re-reads
  `revoked ≠ requested` → **approve 409**. Final `revoked`.
- **approve acquires the lock first:** `requested→approved` (approve 200); revoke re-reads
  `approved`, which **is** revocable → `approved→revoked` (revoke 200). Final `revoked`.
So **revoke is always 200**, **approve is 200-or-409** (ordering-dependent, informational), and
in BOTH orderings the final status is `revoked`. "Both 200" is therefore CORRECT here (it is the
approve-first ordering), not a defect — the discriminator is the final status, never the 200 tally.

## Preconditions
- Live stack `:8000`. Fresh run-stamped org `state-024-<stamp>` + its company_admin.
- ≥50 fresh grants in that org (one platform request each → 50 `requested` grants). No per-org
  uniqueness guard on requests, so one org holds all 50.

## Steps
1. Provision the org; platform requests support access **50 times** → 50 `requested` grants.
2. For each grant, fire `company_approve` and `company_revoke` **concurrently**
   (`asyncio.gather` per grant) so they contend for the same row.
3. Collect approve-results and revoke-results in **separate** lists; `summarize()` each.
4. Read final status of all 50 grants via psql GROUP BY (ordering-independent ground truth).

## Expected result
- Revoke tally: `{200: 50}` (revoke is legal from both `requested` and `approved`).
- Approve tally: `{200: A, 409: 50−A}` for some ordering-dependent A (both values informational).
- **No 500s**, no unhandled exceptions.
- psql ground truth: `status='revoked'` count **= 50**, `status='approved'` count **= 0**.
- No grant left `is_active=true`.

## Harness
Script: `harness/tc_024.py` · run: `docker compose exec -T backend python - < testing/07_break-glass/harness/tc_024.py` (prepend `_common.py`)
psql ground truth: `docker compose exec -T db psql -U oneai -d oneai -c "SELECT status, count(*) FROM support_grant WHERE org_id='<org>' GROUP BY status"`

---

## Execution result

- **Run at:** 2026-06-01 18:14 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> 50 fresh grants, each hit by a concurrent `approve` + `revoke` (`asyncio.gather` per grant).
> **Revoke returned 200 on all 50** (legal from both `requested` and `approved`). **Approve split
> 25×200 / 25×409** — the lock-ordering decides: when approve loses the lock it re-reads the
> committed `revoked`/`approved` and is rejected, when it wins it sets `approved` and the racing
> revoke then legally revokes it. **Zero 5xx.** The ordering-independent invariant held perfectly:
> **all 50 grants ended `revoked`, 0 left `approved`, 0 `is_active=true`** — confirmed both via the
> app inbox and via direct psql GROUP BY on `support_grant`. No revoke was silently overwritten
> back to `approved`.

**Evidence**

```
ORG d7ec3ca6-e54d-4b8d-bfae-25f876de3b9e state-024-19e8464e69f040f
CREATED_GRANTS 50
APPROVE_TALLY {200: 25, 409: 25}
REVOKE_TALLY {200: 50}
ANY_5XX False
INBOX_STATUS_TALLY {'revoked': 50}
INBOX_ACTIVE_COUNT 0
ASSERT all 50 revoked: True
ASSERT zero approved-left-behind: True
ASSERT zero active: True
ASSERT revoke always 200: True
ORG_ID_FOR_PSQL d7ec3ca6-e54d-4b8d-bfae-25f876de3b9e
```

psql ground truth (independent of the app layer):

```
$ docker compose exec -T db psql -U oneai -d oneai -c \
  "SELECT status, count(*) FROM support_grant WHERE org_id='d7ec3ca6-e54d-4b8d-bfae-25f876de3b9e' GROUP BY status ORDER BY status"
 status  | count
---------+-------
 revoked |    50
(1 row)
```

**Verdict**

The defense held under real concurrent load — the lost-update / TOCTOU break-hypothesis (audit
finding #1) is refuted live: not one of the 50 grants was left `approved`+active despite an
accepted revoke. The serialization is owned by `support_grant_repository.py:67` (`get_in_org` …
`.with_for_update()`) — the company transition loader row-locks the grant, so the second
contender blocks, then re-reads the committed status and its service-layer guard
(`company_support_service.py:121/134`) applies to the now-current state. The 25/25 approve split is
ordering noise (informational); the final-status GROUP BY is the proof.

This **CORROBORATES** the audit's `FOR UPDATE` fix rather than independently re-deriving it: this is
a same-row race that serializes on one lock by design (as the repository docstring and the audit
both state). Without `FOR UPDATE` the expected failure is a non-zero `approved` count in the GROUP
BY (a revoke overwritten) — observed count is exactly 0. Confirms PC-05-AC4b.

**Notes / follow-up**

No 5xx and no pool-timeout exceptions at 50 concurrent pairs (100 in-flight requests against a
15+overflow pool) — the per-grant `gather` keeps contention to two requests per row. The
asymmetry (revoke always 200; approve 200-or-409) is the correct, intended state-machine behavior:
revoke is legal from both `requested` and `approved`, so it never loses to an approve — it only
ever transitions the grant to the terminal `revoked` sink. Companion terminal-state cases:
TC-BG-022 (sequential revoke→approve 409).
