# TC-PC-007: Token outlives a deactivated account → both /me and /refresh 401

| Field | Value |
|---|---|
| **ID** | TC-PC-007 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PSES — Session lifecycle |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-02-AC7 (token-outlives-account): an access + refresh token captured while the admin is
ACTIVE must BOTH be rejected (401) once the admin is deactivated — even though the access JWT is
still within its ~15-min cryptographic validity. The live `is_active` re-check is the gate.

## Break hypothesis
A violation = a deactivated admin's still-unexpired access token continues to return 200 at
`/platform/me`, or the refresh token still rotates — i.e. deactivation does not take effect until
the JWT naturally expires (a stateless-token blind spot). That would let a removed staff member
keep platform access for up to a full access-token lifetime, and indefinitely via refresh.

## Preconditions
- Live stack; **PSES owns `tw-lifecycle-tw06012c3@oneai.dev`** (id `87a2a273-…`) and may
  deactivate it. Backend container has no psql, so deactivation runs against the **db** container.
- The harness is **phase-aware** via `/tmp/pses007.txt` (persists across `exec` calls in the
  long-running backend container; `/tmp` is not under `backend/`, so no uvicorn reload):
  phase 1 (no file) captures tokens while active; phase 2 (file present, after psql) re-checks.

## Steps
1. (driver) psql: `UPDATE platform_admins SET is_active=true WHERE email='tw-lifecycle-…'`;
   `rm -f /tmp/pses007.txt` (rerunnable).
2. **Phase 1:** login tw-lifecycle (active) → capture (access, refresh); assert `/platform/me`=200;
   persist tokens to the temp file.
3. (driver) psql: `UPDATE platform_admins SET is_active=false WHERE email='tw-lifecycle-…'`.
4. **Phase 2:** same access → `/platform/me` → expect 401; same refresh → `/platform/refresh`
   → expect 401.

## Expected result
Phase 1 `/platform/me` `200`; after deactivation, the captured access → `/platform/me` `401`
**and** the captured refresh → `/platform/refresh` `401`.

## Harness
Script: `harness/tc_007.py` · run (per phase): `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_007.py | docker compose exec -T backend python -` — bracketed by the psql `is_active` toggles above.

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> While active, the captured token returned the real identity (200). After
> `UPDATE platform_admins SET is_active=false` (psql, verified `is_active=f`), the SAME
> (still-unexpired) access token → `/platform/me` → 401, and the SAME refresh token →
> `/platform/refresh` → 401. The token did not outlive the account.

**Evidence**

```
--- (b) PHASE 1 (active) ---
PHASE-1 LOGIN STATUS: 200
PHASE-1 /platform/me (ACTIVE) STATUS: 200 BODY: {'id': '87a2a273-09fd-43ac-a4ca-12f06638c78f', 'email': 'tw-lifecycle-tw06012c3@oneai.dev', 'full_name': 'Throwaway Lifecycle'}
PHASE-1 TOKENS PERSISTED: True

--- (c) DEACTIVATE via psql ---
UPDATE 1
              email               | is_active
----------------------------------+-----------
 tw-lifecycle-tw06012c3@oneai.dev | f

--- (d) PHASE 2 (deactivated) ---
PHASE-2 /platform/me (DEACTIVATED) STATUS: 401 BODY: {'detail': 'Invalid email or password.'}
PHASE-2 /platform/refresh (DEACTIVATED) STATUS: 401 BODY: {'detail': 'Invalid email or password.'}
TOKEN-DIES-WITH-ACCOUNT: True
```

**Verdict**

Defense held. Both gates re-check `is_active` against the live DB row, not just the JWT:
`build_admin_view_by_id` raises `InvalidCredentialsError` when `admin is None or not
admin.is_active` (`platform_auth_service.py:136-138`), and `refresh` does the same after
`consume` (`platform_auth_service.py:108-110`). Deactivation takes effect immediately for both
identity resolution and refresh — PC-02-AC7 confirmed live.

**Notes / follow-up**

This case deliberately leaves `tw-lifecycle-…` **deactivated** (PSES owns it). Note the scope
boundary vs FIX_BEFORE_PROD "access-token denylist": deactivation is enforced because BOTH
platform read paths hit the DB row; a *company* access token (no per-request DB re-check on
plain GETs) is the case that denylist item targets — not in PSES scope.
