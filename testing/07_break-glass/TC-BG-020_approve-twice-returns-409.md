# TC-BG-020: Approve-twice → 409 (no time-box re-extension)

| Field | Value |
|---|---|
| **ID** | TC-BG-020 · **Suite** STATE · **Type** Negative · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
APPROVE#1 200 approved expires_at=2026-06-01T22:14:01Z | APPROVE#2 409 "Cannot decide a grant that is already approved."
FINAL expires_at UNCHANGED (no re-extension)
```
**Verdict:** Defense held. `_load_requested` requires `status=='requested'` (`company_support_service.py:134`);
the rejected second approve mutated nothing (time box not re-extended). PC-05-AC4.
