# TC-BG-002: No platform path can produce `approved` — consent is structural

| Field | Value |
|---|---|
| **ID** | TC-BG-002 · **Suite** CONSENT · **Type** Adversarial · **Severity if fail** Critical |
| **Result** | ✅ Pass · **Tag** — (NA, contract) · **Status** Executed |

## Objective
The only path to `approved` is the company approve endpoint — a platform admin cannot self-approve.

## Execution result (2026-06-01)
**Evidence**
```
live OpenAPI approve routes == ['/support-access/{grant_id}/approve']
/platform/* containing 'approve' == []
probe POST /platform/support-requests/{id}/approve == 404
company approve (real admin) == 200 approved
```
**Verdict:** PC-05-AC2 holds. `PlatformSupportService` has no approve method (`platform_support_service.py:60-105`);
approval lives only on the company router gated by `require_company_admin`. The structural property is
unconditional; the forged-token vector is isolated to TC-BG-003. Defense held.
