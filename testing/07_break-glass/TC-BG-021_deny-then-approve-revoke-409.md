# TC-BG-021: `denied` is terminal — approve 409, revoke 409

| Field | Value |
|---|---|
| **ID** | TC-BG-021 · **Suite** STATE · **Type** Negative · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
DENY 200 denied | APPROVE-AFTER-DENY 409 "already denied" | REVOKE-AFTER-DENY 409 "is denied" | FINAL denied
```
**Verdict:** Defense held. A refused request can't be resurrected into access: `_load_requested` blocks
approve/deny and `_REVOCABLE={requested,approved}` excludes `denied` (`company_support_service.py:134,:48,:121`).
`denied` is a true sink. PC-05-AC4.
