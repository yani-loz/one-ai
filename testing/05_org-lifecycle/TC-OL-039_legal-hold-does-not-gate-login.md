# TC-OL-039: Legal hold is metadata only — does NOT gate login/refresh

| Field | Value |
|---|---|
| **ID** | TC-OL-039 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Positive / Negative (asymmetry characterization) |
| **Severity if it fails** | Info (behaviour-defining; a gate here would be a NEW behaviour, not a defect) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Setting `legal_hold=true` on an org does NOT block its company users from logging in or
refreshing. Legal hold blocks **erasure** in PC-06, not authentication today — only
`status='suspended'` gates auth (auth_service `_load_loginable_org`).

## Break hypothesis
Legal hold is wired into the auth gate by mistake (or conflated with suspension), so a held
org's users are locked out — a behaviour not in the spec (legal hold ≠ access cutoff).

## Preconditions
Live stack. Fresh run-stamped org (`contract39-<stamp>`) with a known admin. Demo platform
token. Restored to `legal_hold=false`.

## Steps
1. Platform-login; `provision_company(prefix="contract39")`.
2. PATCH legal-hold `true` on MY org.
3. Company-login that org's admin → expect 200.
4. Refresh that login's refresh token → expect 200.
5. Restore `legal_hold=false`.

## Expected result
Login 200 and refresh 200 while `legal_hold=true` — legal hold is auth-inert.

## Harness
Script: `harness/tc_039.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_039.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> With `legal_hold=true` on the org, the company admin logged in (200) and refreshed (200)
> normally. Legal hold did not gate authentication — only suspension does.

**Evidence**

```
PATCH legal_hold=true -> 200 legal_hold=True
company login under legal_hold=true -> 200 (expect 200)
refresh under legal_hold=true -> 200 (expect 200)
```

**Verdict**

The defense held / behaviour is as documented. The auth gate
`AuthService._load_loginable_org`
(`backend/app/identity/services/auth_service.py:144-162`) keys ONLY on
`organization.status == 'suspended'`; it never reads `legal_hold`. So a legal hold is
metadata that blocks erasure (PC-06), not login — confirms the EPIC's PC-06 scoping. Tagged
CONFIRMS-DOCUMENTED (the legal-hold-≠-auth-cutoff design is the documented contract).

**Notes / follow-up**

Org restored to `legal_hold=false`. Complements TC-OL-036 (persist) by proving the persisted
flag is auth-inert.
