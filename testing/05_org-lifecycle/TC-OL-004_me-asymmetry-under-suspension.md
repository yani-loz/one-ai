# TC-OL-004: `/auth/me` still 200 under suspension (the deliberate asymmetry)

| Field | Value |
|---|---|
| **ID** | TC-OL-004 · **Target** Org Lifecycle (PC-03a) · **Suite** SUSPEND ⭐ |
| **Type** | Adversarial · **Severity if fail** Medium · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-FIXED (PC-03a-AC4) |

## Objective
A valid company **access** token issued before suspension keeps working on `/auth/me` while the org is
suspended — the access path is deliberately ungated (only login + refresh gate on the org status).

## Break hypothesis
If `build_authenticated_user_by_id` re-checked org status, `/auth/me` would 403 under suspension. It does
not (`auth_service.py:114-132` — no `_load_loginable_org` call), so the pre-suspension token stays 200.

## Steps / Harness
`provision_company("sus004")` → suspend → `GET /auth/me` with the pre-suspension access token.
`harness/_finish_suspend.py` (case 004).

## Execution result
- **Run at:** 2026-06-01 local · **Result:** ✅ Pass · **Tag:** CONFIRMS-FIXED

**Evidence**
```
[004] /auth/me (pre-susp access) under suspension: 200 (expect 200 — asymmetry)
```

**Verdict**
Defense held — and it is a *deliberate* asymmetry, not a leak. The company access-token path
(`get_current_principal` → `build_authenticated_user_by_id`) never re-reads `organizations.status`; only
`AuthService.login`/`refresh` call `_load_loginable_org`. So suspension is **immediate for new sessions,
eventual for in-flight ones** — see TC-OL-005 for the full blast radius. PC-03a-AC4 confirmed live.

**Notes** Pairs with TC-OL-005 (the same property extended to `/users`).
