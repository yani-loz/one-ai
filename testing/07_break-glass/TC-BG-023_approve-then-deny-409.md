# TC-BG-023: Approve-then-deny → 409 (deny requires `requested`)

| Field | Value |
|---|---|
| **ID** | TC-BG-023 · **Suite** STATE · **Type** Negative · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
APPROVE 200 approved | DENY-AFTER-APPROVE 409 "Cannot decide a grant that is already approved."
FINAL approved is_active=True (rejected deny mutated nothing)
```
**Verdict:** Defense held. Deny shares the `_load_requested` gate with approve
(`company_support_service.py:103→134`), so `approved` has only revoke/expiry as exits — deny is not a
guard-bypass shortcut. Completes the illegal-transition matrix. PC-05-AC4.
