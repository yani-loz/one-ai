# TC-BG-022: `revoked` is terminal — approve 409, revoke-again 409 (revoke-of-approved happy path too)

| Field | Value |
|---|---|
| **ID** | TC-BG-022 · **Suite** STATE · **Type** Negative · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
APPROVE 200 approved active | REVOKE 200 revoked is_active=False | APPROVE-AFTER-REVOKE 409 "already revoked"
REVOKE-AGAIN 409 "is revoked" | FINAL revoked inactive
```
**Verdict:** Defense held. A deliberate early cut-off is permanent — a revoked window can't be re-opened with
a fresh 4h box. Corroborates PR-5 review fix #4 (revoke-of-approved happy path) + PC-05-AC4. `revoked` is the
second sink state.
