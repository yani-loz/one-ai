# TC-OL-007: PATCH status drives the login gate end-to-end (AC5)

| Field | Value |
|---|---|
| **ID** | TC-OL-007 · **Target** Org Lifecycle (PC-03a) · **Suite** SUSPEND ⭐ |
| **Type** | Adversarial (e2e) · **Severity if fail** High · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-FIXED (PC-03a-AC5) |

## Objective
Prove the write reaches the auth gate end-to-end: suspend via the real endpoint → a company login is blocked
→ reactivate via the endpoint → login works again.

## Break hypothesis
If the PATCH wrote to a different field/session than the one login reads, or didn't commit, the login gate
would not flip. It does — the same `organizations.status` column is the single source of truth.

## Steps / Harness
`provision_company("sus007")` → `PATCH .../status {suspended}` → login → `PATCH .../status {active}` → login.
`harness/_finish_suspend.py` (case 007).

## Execution result
- **Run at:** 2026-06-01 local · **Result:** ✅ Pass · **Tag:** CONFIRMS-FIXED

**Evidence**
```
[007] e2e: PATCH suspend=200 -> login=403 -> PATCH reactivate=200 -> login=200
```

**Verdict**
Defense held end-to-end. `PlatformOrgService.set_status` flips `organizations.status` and the `get_session`
unit-of-work commits; `AuthService.login._load_loginable_org` reads that same column on the next login →
403 when suspended, 200 when active. The PATCH endpoint genuinely drives the company auth gate (PC-03a-AC5).

**Notes** The endpoint-driven twin of TC-OL-001/003 (which suspend via the helper).
