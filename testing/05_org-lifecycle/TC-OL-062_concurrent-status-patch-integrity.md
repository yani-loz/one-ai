# TC-OL-062: Concurrent status PATCH integrity (same-row) — no corruption, no 5xx

| Field | Value |
|---|---|
| **ID** | TC-OL-062 · **Target** Org Lifecycle (PC-03a) · **Suite** RACE |
| **Type** | Concurrency · **Severity if fail** Low · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-FIXED |

## Objective
Concurrent status PATCHes on one org never corrupt the column or 500 — the final value is always a valid enum.

## Steps / Harness
`provision_company("race062")` → 40 concurrent `PATCH .../status` alternating `suspended`/`active` →
`GET detail`. `harness/_finish_race.py` (062).

## Execution result
- **Run at:** 2026-06-01 local · **Result:** ✅ Pass · **Tag:** CONFIRMS-FIXED

**Evidence**
```
[062] 40 concurrent PATCH status alternating: {200: 40}; final status=suspended valid_enum=True
```

**Verdict**
Defense held. 40/40 → 200, zero 5xx; the final `status` is a valid enum value. This is a **same-row** race that
serializes on the row lock by design (each PATCH is `load → set attribute → commit`), so the result is
last-writer-wins with no torn writes — **corroboration** of the single-column update, not independent proof.
The DB `CHECK (ck_organizations_status)` is the backstop that would reject any invalid value. Org reactivated
to `active` after. (Lightweight — same-row contention is benign here.)
