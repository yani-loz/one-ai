<!--
  TC-ER-004 — AC1b: row-lock TOCTOU corroboration (erase vs set_legal_hold race). Suite HOLD.
-->

# TC-ER-004: Row-lock TOCTOU — concurrent erase vs set_legal_hold is always self-consistent

| Field | Value |
|---|---|
| **ID** | TC-ER-004 |
| **Target** | 08 — GDPR erasure + compliance export (PC-06) |
| **Suite** | HOLD — legal-hold-beats-erasure + slug guard |
| **Type** | Concurrency / Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Corroborate ⭐ PC-06-AC1b: the `FOR UPDATE` row lock (`get_for_update`) serializes a concurrent
`set_legal_hold(True)` against an in-flight erase, so a hold placed as a purge looms is never
overwritten by an erase reading a stale `legal_hold` flag. This is the High-severity TOCTOU the
PR-6 review fixed. A deterministic end-state assertion is INFEASIBLE (the two correct orderings
— hold-wins-409-org-intact, and erase-wins-then-hold-applies-to-the-offboarded-shell — share an
end state), so the assertion is on SELF-CONSISTENCY + no 5xx, not on which side won.

## Break hypothesis
Without serialization, the erase reads `legal_hold=false`, then the hold commits in the window,
then the erase deletes — yielding the impossible state **users gone WHILE the erase reported 409
(hold blocked)**, or **users gone while status still `active`** (a torn/partial erase). Or the
race throws a deadlock/serialization 5xx. Any of these is the finding.

## Preconditions
- Live stack `:8000`. A FRESH run-stamped org per iteration (slug `hold-er4-<i>-<stamp>`), ≥30
  iterations. Each erase carries a valid slug + the platform password (so it can reach the
  legal-hold guard).
- Per iteration the two ops race on the SAME org row: `erase_org(confirm_slug=slug)` and
  `patch_legal_hold(true)` fired via `asyncio.gather(..., return_exceptions=True)`.

## Steps
1. For i in range(≥30): provision a fresh org.
2. Fire `erase` + `patch_legal_hold(true)` concurrently; capture BOTH status codes.
3. After each iteration read ground truth (users-remaining, status) and classify the outcome:
   - **hold-wins:** erase==409 ⇒ assert users intact AND status `active`.
   - **erase-wins:** erase==200 ⇒ assert users==0 AND status `offboarded`.
   - Forbidden: erase==409 with users gone; users gone with status≠offboarded; any 5xx from
     either op; a torn state.
4. Tally the spread (hold-wins / erase-wins / 5xx / inconsistent).

## Expected result
- Across all iterations: **zero 5xx**, **zero inconsistent** end states. Every iteration is
  either a clean hold-wins (409 + intact + active) or a clean erase-wins (200 + 0 users +
  offboarded). The legal-hold patch landing on an already-offboarded org may return a 4xx — that
  is benign, not a defect.

## Harness
Script: `harness/tc_004.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_004.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Across 32 concurrent iterations: **32/32 erase-wins (200), 0 hold-wins, 0 5xx, 0 inconsistent,
> 0 exceptions.** Every erase-wins iteration was self-consistent (users == 0 AND status ==
> `offboarded`). The concurrent `patch_legal_hold(true)` returned 200 every time, landing on the
> now-offboarded shell (benign). The hold-wins ordering did not manifest in this timing — its
> correctness is proven sequentially in TC-ER-001.

**Evidence**

```
iterations: 32
erase_status_spread: {200: 32}
hold_status_spread: {200: 32}
tally: {'hold_wins': 0, 'erase_wins': 32, 'other_erase_code': 0, '5xx': 0, 'inconsistent': 0, 'exc': 0}
anomalies: none
VERDICT: PASS
```

**Verdict**

The lock held under real concurrency: every iteration landed in a correct, self-consistent end
state, with **zero 5xx** and **zero inconsistent** outcomes. No iteration produced the TOCTOU
signature (users gone while erase reported 409, or a torn `active`-but-emptied org). The spread
was one-sided (erase won the lock first every time); the hold-wins-under-concurrency branch did
not occur in this run, but its correctness is established sequentially in TC-ER-001. This
CORROBORATES the `FOR UPDATE` fix (`organization_repository.py:39-51`, consumed by
`erasure_service.py:91`) — same-row lock by design — rather than being independent proof, exactly
as the review's concurrency note states. CONFIRMS-FIXED for AC1b.

**Notes / follow-up**

All 32 iteration orgs are run-stamped and were erased (offboarded) by the race itself — no shared
data touched. The erase now holds the row lock through a bcrypt password verify (slug → password
→ hold), which lengthens the locked section; this does not affect correctness.
