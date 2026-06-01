# TC-PC-004: Logout revokes the refresh token

| Field | Value |
|---|---|
| **ID** | TC-PC-004 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PSES — Session lifecycle |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-02-AC4 (logout revokes): after `POST /platform/logout` (204), the same refresh token
can no longer rotate at `/platform/refresh` (→ 401).

## Break hypothesis
A violation = the refresh token still rotates after logout (logout did not revoke), so a
"logged out" session is still alive server-side.

## Preconditions
- Live stack; demo platform admin seeded. PSES suite; no orgs created.

## Steps
1. `platform_login_pair()` → (_, refresh).
2. `POST /platform/logout` with `{refresh_token: refresh}` → expect 204 (empty body).
3. `POST /platform/refresh` with the same refresh → expect 401.

## Expected result
Logout `204` empty body; subsequent refresh `401`.

## Harness
Script: `harness/tc_004.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_004.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Logout returned 204 with an empty body; the refresh token then failed to rotate (401).

**Evidence**

```
LOGOUT STATUS: 204 BODY: ''
REFRESH-AFTER-LOGOUT STATUS: 401 BODY: {'detail': 'Refresh token is invalid.'}
LOGOUT-REVOKES: True
```

**Verdict**

Defense held. `PlatformAuthService.logout` → `TokenRotator.revoke`
(`token_rotator.py:66-68`) sets `revoked_at`; the subsequent `consume` sees zero unrevoked
rows → 401. PC-02-AC4 (logout) confirmed live.

**Notes / follow-up**

Logout revokes the *refresh* token only; the already-issued *access* token remains valid for
its ~15-min TTL (stateless JWT, no denylist — tracked in FIX_BEFORE_PROD "access-token
denylist"). Not in scope for this case.
