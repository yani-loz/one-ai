<!--
  Test-case: TC-BG-023 — deny requires `requested` (AC4 state machine).
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-023: Approve then deny → 409 (deny requires `requested`)

| Field | Value |
|---|---|
| **ID** | TC-BG-023 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | STATE — transition state-machine + concurrent row-lock |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify deny is guarded by current state and shares the `_load_requested` gate with approve: an
already-`approved` grant cannot be denied. PC-05-AC4 — deny requires `requested`. The correct
way to cut off an approved grant is revoke, not deny; deny on a non-`requested` grant → **409**.

## Break hypothesis
The attacker's bet: deny has a different (weaker) guard than approve, or none, so an approved
grant can be denied. If deny succeeded on an `approved` grant, the state machine would have two
paths out of `approved` (revoke and deny), muddying the "approved → active until revoked/expired"
invariant and the audit narrative. A failure: deny-after-approve → 200.

## Preconditions
- Live stack `:8000`. Fresh run-stamped org `state-023-<stamp>` + its company_admin.
- One fresh grant taken to `approved`: request → approve (200).

## Steps
1. Platform requests support access → grant `requested` (201).
2. Company_admin **approves** → 200, `status='approved'`.
3. Company_admin **denies** the approved grant → expect **409**.

## Expected result
- Approve: 200, `status='approved'`.
- Deny-after-approve: **409** ("Cannot decide a grant that is already approved.").
- Status remains `approved`, `is_active` stays true (rejected deny mutates nothing).

## Harness
Script: `harness/tc_023.py` · run: `docker compose exec -T backend python - < testing/07_break-glass/harness/tc_023.py` (prepend `_common.py`)

---

## Execution result

- **Run at:** 2026-06-01 18:14 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Approve on a `requested` grant succeeded (200, `approved`, active). Denying the now-`approved`
> grant was rejected **409** (`Cannot decide a grant that is already approved.`). Deny shares the
> `_load_requested` gate with approve, so `approved` is not a legal deny source — the only way out
> of `approved` is revoke or expiry. Final status stayed `approved`, `is_active` true.

**Evidence**

```
ORG c0514d26-b0e8-4bb8-98c6-5191c87e7795 state-023-19e8464c637ea01
REQUEST 201 status= requested
APPROVE 200 status= approved is_active= True
DENY-AFTER-APPROVE 409 body= {'detail': 'Cannot decide a grant that is already approved.'}
FINAL status= approved is_active= True
ASSERT approve==200: True
ASSERT deny-after-approve==409: True
ASSERT final status still approved & active: True
```

**Verdict**

The defense held. Deny is state-guarded identically to approve (both require `requested`) — the
break-hypothesis (deny has a weaker/absent guard, giving `approved` two exit paths) is refuted.
Guard: `company_support_service.py:103` (`deny` calls `_load_requested`) → `:134` raises 409 when
status is not `requested`. The 409 detail correctly names the offending state (`approved`).
Confirms PC-05-AC4 holds live and that the deny path is not a guard-bypass shortcut.

**Notes / follow-up**

Completes the illegal-transition matrix for this suite alongside TC-BG-020/021/022: approve and
deny both pin to `requested`; revoke pins to `{requested, approved}`. The rejected deny mutates
no field (no phantom `support.denied` audit event on an approved grant).
