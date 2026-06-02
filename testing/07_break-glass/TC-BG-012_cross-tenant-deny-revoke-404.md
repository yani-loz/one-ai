# TC-BG-012: B-admin deny/revoke of A's grant → 404 (org filter precedes the state guard)

| Field | Value |
|---|---|
| **ID** | TC-BG-012 · **Suite** ISO · **Type** Negative/Adversarial · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
B deny (requested) → 404 | A approve → 200 | B revoke (APPROVED) → 404 (NOT 409/200) | A revoke → 200 revoked
psql final = revoked
```
**Verdict:** Defense held — discriminating. The cross-org revoke of an *approved* grant is **404, not 409**,
proving the org filter (`get_in_org`) fires before the state guard (no status-based existence leak). Positive
control: A-admin revoked the same grant → 200. PC-05-AC3 + review finding #3.
