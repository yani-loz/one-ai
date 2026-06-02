# TC-BG-030: Audience confinement — company token on `/platform/*` and platform token on `/support-access/*` both 401

| Field | Value |
|---|---|
| **ID** | TC-BG-030 · **Suite** AEA · **Type** Negative/Adversarial · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
company_token → POST /platform/.../support-requests : 401 "Access token is invalid."
platform_token → POST /support-access/{gid}/approve  : 401 | grant still requested
```
**Verdict:** Defense held both directions, before any state change. `get_current_platform_admin` binds
`aud='platform'` and `get_current_principal` binds `aud='company'` (`dependencies.py`). The gate fires before
the service loads the grant. PC-05-AC6.
