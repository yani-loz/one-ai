# TC-PC-005: Logout idempotency + no enumeration (unknown token → 204)

| Field | Value |
|---|---|
| **ID** | TC-PC-005 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PSES — Session lifecycle |
| **Type** | Boundary |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove logout is idempotent and non-enumerating: logging out the SAME token twice → 204 both
times, and logging out an UNKNOWN random token → 204 (no 404/400 that would reveal whether a
token exists).

## Break hypothesis
A violation = the second logout or the unknown-token logout returns a non-204 (e.g. 404/400/500),
creating a token-existence oracle or breaking idempotency.

## Preconditions
- Live stack; demo platform admin seeded. PSES suite; no orgs created.
- The unknown token is a fresh `uuid4()` string (a value that is not a stored token hash).

## Steps
1. `platform_login_pair()` → (_, refresh).
2. `POST /platform/logout` with refresh (x2) → expect 204, 204.
3. `POST /platform/logout` with `str(uuid4())` → expect 204.

## Expected result
All three logout calls return `204` with empty body.

## Harness
Script: `harness/tc_005.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_005.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Both logouts of the real token, and the logout of a never-seen random token, all returned
> 204 with empty bodies — fully idempotent and non-enumerating.

**Evidence**

```
LOGOUT-1 STATUS: 204 BODY: ''
LOGOUT-2 STATUS: 204 BODY: ''
LOGOUT-UNKNOWN-TOKEN: e2955402-aa6c-449d-9fce-125de8cc24b3
LOGOUT-UNKNOWN STATUS: 204 BODY: ''
IDEMPOTENT-NO-ENUMERATION: True
```

**Verdict**

Defense held. `TokenRotator.revoke` (`token_rotator.py:66-68`) is an unconditional
`revoke_by_hash` whose zero-row result is ignored — so an unknown or already-revoked token is a
silent no-op (204). No existence oracle. The route fixes `status_code=204`
(`platform_routes.py:79`).

**Notes / follow-up**

`RefreshRequest`/`LogoutRequest` require `min_length=1` `refresh_token` (`extra=forbid`), so an
empty/missing field is a 422 (a separate validation boundary, not this case's scope).
