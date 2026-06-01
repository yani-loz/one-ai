<!--
  Test-case: TC-BG-022 — revoked is terminal (AC4 state machine).
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-022: Revoked grant is terminal → approve 409, revoke-again 409

| Field | Value |
|---|---|
| **ID** | TC-BG-022 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | STATE — transition state-machine + concurrent row-lock |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify `revoked` is terminal: after a company_admin revokes an **approved** (active) grant,
it can be neither re-approved nor revoked again. PC-05-AC4 — once access is cut off, it stays
cut off. Both follow-ups must be **409**.

## Break hypothesis
The attacker's bet: a revoked grant is re-approved, re-opening a window that was deliberately
closed early. If approve checks only "not approved" (a revoked grant is not approved), it would
re-activate → access restored after an explicit cut-off, with a fresh 4h box. A failure:
approve-after-revoke → 200 (`is_active` back to true) or revoke-again → 200.

## Preconditions
- Live stack `:8000`. Fresh run-stamped org `state-022-<stamp>` + its company_admin.
- One fresh grant taken to `approved` then `revoked`: request → approve (200) → revoke (200).

## Steps
1. Platform requests support access → grant `requested` (201).
2. Company_admin **approves** → 200, `status='approved'`, `is_active=true`.
3. Company_admin **revokes** the approved grant → 200, `status='revoked'`, `is_active=false`.
4. Company_admin **approves** the revoked grant → expect **409**.
5. Company_admin **revokes** the revoked grant again → expect **409**.

## Expected result
- Approve: 200 (`approved`, active). Revoke: 200 (`revoked`, `is_active=false`).
- Approve-after-revoke: **409** ("Cannot decide a grant that is already revoked.").
- Revoke-again: **409** ("Cannot revoke a grant that is revoked.").
- Status remains `revoked`; `is_active` stays false throughout.

## Harness
Script: `harness/tc_022.py` · run: `docker compose exec -T backend python - < testing/07_break-glass/harness/tc_022.py` (prepend `_common.py`)

---

## Execution result

- **Run at:** 2026-06-01 18:14 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Grant taken to `approved` (200, active) then `revoked` (200, `is_active=false`). Both follow-ups
> on the revoked grant rejected: approve → **409** (`Cannot decide a grant that is already
> revoked.`), revoke-again → **409** (`Cannot revoke a grant that is revoked.`). A deliberately
> closed window cannot be re-opened. Final status `revoked`, `is_active` false.

**Evidence**

```
ORG 9f7f43e6-cf0b-4d75-b7ee-1cb70aa839a3 state-022-19e8464b82dd699
REQUEST 201 status= requested
APPROVE 200 status= approved is_active= True
REVOKE 200 status= revoked is_active= False
APPROVE-AFTER-REVOKE 409 body= {'detail': 'Cannot decide a grant that is already revoked.'}
REVOKE-AGAIN 409 body= {'detail': 'Cannot revoke a grant that is revoked.'}
FINAL status= revoked is_active= False
ASSERT approve==200: True
ASSERT revoke==200: True
ASSERT approve-after-revoke==409: True
ASSERT revoke-again==409: True
ASSERT final status revoked & inactive: True
```

**Verdict**

The defense held. An early cut-off (revoke) is permanent — the break-hypothesis (revoked grant
re-approved, restoring a fresh 4h box) is refuted. Guards: `company_support_service.py:134`
(`_load_requested` blocks approve on non-`requested`) and `:121` (`_REVOCABLE` excludes
`revoked`). Confirms PC-05-AC4 holds live. Note the happy-path revoke-of-approved (audit fix #4)
also fired correctly here: `approved → revoked`, `is_active` flips to false.

**Notes / follow-up**

`revoked` is the second sink state (companion to `denied`, TC-BG-021). `is_active=false` after
revoke comes from the live computation (`grant_is_active` requires `status=='approved'`), not a
stored flag — the cut-off is immediate, independent of `expires_at`.
