# TC-BG-032: Expiry is terminal — an expired (approved-but-past) grant cannot be re-approved (409)

| Field | Value |
|---|---|
| **ID** | TC-BG-032 · **Suite** AEA · **Type** Negative/Adversarial · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
re-approve the expired (status=approved, expires_at past) grant → 409 "Cannot decide a grant that is already approved."
expires_at UNCHANGED (window not resurrected)
```
**Verdict:** Defense held. The approve path's `_load_requested` requires `status=='requested'`, which an
expired-approved grant fails (`company_support_service.py:129-138`). The only re-grant path is a fresh
request + fresh consent — an expired window can't be reopened. Rounds out the live-expiry story (TC-BG-031).
