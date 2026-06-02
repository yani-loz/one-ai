# TC-BG-033: Audit emission — all four `support.*` transitions logged; `support.approved` carries `expires_at`

| Field | Value |
|---|---|
| **ID** | TC-BG-033 · **Suite** AEA · **Type** Positive/Adversarial (completeness) · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
after request→approve, request→deny, request→revoke on one org:
org audit actions ⊇ {support.requested, support.approved, support.denied, support.revoked}
support.approved details = {'expires_at':'2026-06-01T22:13:11Z', 'window_hours':4.0}
```
**Verdict:** Defense held. Each transition records same-session as its state change
(`platform_support_service.py:107-125`, `company_support_service.py:84-92,149-167`); `support.approved`
carries `expires_at` so "logged → expire" holds without a discrete expiry event. PC-05-AC8.
