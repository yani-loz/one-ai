<!--
  XDOM suite — cross-domain confinement (the crown jewel). Author top half BEFORE running,
  write the Execution result block back AFTER running. See ../README.md for legend/tags.
-->

# TC-PC-020: Company-aud token with a REAL platform-admin sub is rejected on `GET /platform/me`

| Field | Value |
|---|---|
| **ID** | TC-PC-020 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial (DISCRIMINATING) |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-02-AC3a **discriminatingly**: a company-audience access token whose `sub` is the
REAL demo platform admin's id must be rejected (401) at `GET /platform/me`. The audience
check (`decode_access_token(..., PLATFORM_AUDIENCE)`) must be the **sole** reason for the
401 — every other claim (`sub`, `role`, `org_id`) is well-formed and resolves to a real,
active platform admin, so deleting the audience guard would flip this to 200.

## Break hypothesis
If `get_current_platform_admin` did not pin the audience to `platform`, a company token
carrying a real admin id would sail through `decode_access_token` (same HS256 dev secret),
build a valid Principal, and `build_admin_view_by_id` would find the real, active admin →
**200 with the real admin identity**. This is the test-1 false-green that the discriminating
construction is designed to expose: a random-sub company token 401s via the *secondary*
admin-not-found path even with the guard removed, proving nothing.

## Preconditions
- Live stack up; demo platform admin `super@ethera.ai` exists and is active.
- Run-stamp: this script fetches the **real** admin id at runtime (`/platform/login` →
  `GET /platform/me`) — never hardcoded, so it survives a reseed.
- `forge_company_token(sub=<real admin id>, org_id=<any uuid>)` mints the hostile token
  (aud='company', role='company_admin', dev secret).

## Steps
1. `platform_login_pair` → real platform access token.
2. `GET /platform/me` with the real token → capture `id` (the real admin id) + 200 proof.
3. Forge a **company-aud** token with `sub = <that real admin id>`, any `org_id`.
4. `GET /platform/me` with the forged company token.

## Expected result
- Step 2: `200` `{id,email,full_name}` (control — the sub is a real, active admin).
- Step 4: `401` with the invalid-token detail (audience mismatch), **never** 200, never a
  not-found body, never 500.

## Harness
Script: `harness/tc_020.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_020.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The real platform token returned the real admin identity (the control proving the sub is a
> live, active admin). The forged **company-aud** token carrying that exact same real admin id
> was rejected at `GET /platform/me` with `401 {"detail":"Access token is invalid."}`. Because
> the sub/role/org_id are all valid and resolve to a real active admin, the audience guard is
> the only thing that produced the 401 — removing it would yield 200. Discrimination confirmed.

**Evidence**

```
== TC-PC-020 — company-aud token w/ REAL admin sub -> /platform/me (DISCRIMINATING) ==
[control] GET /platform/me (real platform token): 200
          body: {'id': '609f2b17-bee9-4f7f-a26d-cb08f666497a', 'email': 'super@ethera.ai', 'full_name': 'Ethera Super Admin'}
[forge]   real admin id used as company-token sub: 609f2b17-bee9-4f7f-a26d-cb08f666497a
[attack]  GET /platform/me (FORGED company-aud token, sub=real admin id): 401
          body: {'detail': 'Access token is invalid.'}
RESULT: PASS — audience guard is load-bearing (401 is audience, not not-found)
```

**Verdict**

The defense held. The audience check in `get_current_platform_admin`
(`backend/app/identity/dependencies.py:116` → `decode_access_token(credentials.credentials,
PLATFORM_AUDIENCE)` in `security/tokens.py:77-83`, with `audience=PLATFORM_AUDIENCE`) is the
**load-bearing** boundary. With a real-admin sub, the only failure mode left is the audience
mismatch — confirming PR-2 test-1's hardening holds against the live server, not just unit
tests. CONFIRMS-FIXED (PC-02-AC3a).

**Notes / follow-up**
Discriminating twin of TC-PC-026 (which adds an escalated `role` claim to prove the boundary
is the *audience*, not the role). No data mutated; the real demo admin was only read.
