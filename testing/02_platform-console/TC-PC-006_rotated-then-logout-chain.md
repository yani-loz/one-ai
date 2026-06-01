# TC-PC-006: Rotated-then-logout chain composes (old→401, new logged out→401)

| Field | Value |
|---|---|
| **ID** | TC-PC-006 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PSES — Session lifecycle |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove rotation and logout **compose**: after rotating (old refresh → 401) and then logging out
the NEW refresh token, the new token also no longer rotates (→ 401). No live refresh token
survives the chain.

## Break hypothesis
A violation = after logging out the rotated-in NEW token, it still rotates — i.e. logout
revokes by the wrong key, or rotation issues a token logout cannot find, leaving a live session.

## Preconditions
- Live stack; demo platform admin seeded. PSES suite; no orgs created.

## Steps
1. `platform_login_pair()` → (_, old_refresh).
2. `POST /platform/refresh` old_refresh → 200; capture new_refresh.
3. `POST /platform/refresh` old_refresh again → 401 (old is dead).
4. `POST /platform/logout` new_refresh → 204.
5. `POST /platform/refresh` new_refresh → 401 (new is dead).

## Expected result
Rotate 200; old-reuse 401; logout-new 204; new-after-logout 401.

## Harness
Script: `harness/tc_006.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_006.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The old token died on rotation (401), the new token logged out (204), and the new token then
> failed to rotate (401). The chain left no live refresh token.

**Evidence**

```
ROTATE STATUS: 200
OLD-REUSE STATUS: 401 BODY: {'detail': 'Refresh token is invalid.'}
LOGOUT-NEW STATUS: 204 BODY: ''
NEW-REFRESH-AFTER-LOGOUT STATUS: 401 BODY: {'detail': 'Refresh token is invalid.'}
ROTATION+LOGOUT-COMPOSE: True
```

**Verdict**

Defense held. The new token's hash is what `issue_pair` stored and what `revoke` targets, so
logout and rotation key on the same `sha256_hex(raw)` (`tokens.py:101-103`, `token_rotator.py`).
Rotation single-use (AC4) and logout revocation compose correctly — no orphaned live session.

**Notes / follow-up**

Strengthens TC-PC-003/004 by proving the two revocation paths interact correctly across a
rotation boundary.
