<!--
  XDOM suite — cross-domain confinement (the crown jewel). See ../README.md for legend/tags.
-->

# TC-PC-026: Role-vs-audience — a company-aud token with `role=platform_admin` is still rejected

| Field | Value |
|---|---|
| **ID** | TC-PC-026 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial (DISCRIMINATING) |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove the domain boundary is the **audience claim, not the role claim**: a company-aud token
that self-promotes its `role` to `platform_admin` AND carries the REAL demo admin's id as `sub`
must still be rejected (401) at `GET /platform/me`. A company token cannot escalate into the
platform domain by lying about its role.

## Break hypothesis
If the platform gate trusted the `role` claim instead of (or in addition to) the audience, a
company-aud token with `role=platform_admin` would pass. The discriminating element is the
REAL admin sub: with a random sub the 401 would false-green via admin-not-found even if the
audience guard were removed (test-1 anti-pattern). With the real admin sub, removing the
audience check would yield 200 — so a 401 proves the audience (not the role) is load-bearing.

## Preconditions
- Live stack up; real demo platform admin id fetched at runtime via `/platform/me` (not
  hardcoded — robust to reseed).
- Forged token: `forge_company_token(sub=<real admin id>, org_id=<any uuid>,
  role="platform_admin")` (aud='company', dev secret).

## Steps
1. `platform_login_pair` → `GET /platform/me` → capture the real admin id (control: 200).
2. Forge a company-aud token with `role="platform_admin"` and `sub=<real admin id>`.
3. `GET /platform/me` with the forged token.

## Expected result
- Step 1 control: `200`.
- Step 3: `401 {"detail":"Access token is invalid."}` — the audience guard rejects regardless
  of the escalated role claim. Never 200.

## Harness
Script: `harness/tc_026.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_026.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> A company-aud token carrying `role=platform_admin` and the real demo admin's id was rejected
> at `GET /platform/me` with 401. Since the sub is a real active admin (control returned 200),
> the only thing producing the 401 is the audience check — the escalated role claim did not help.
> The boundary is the audience, not the role.

**Evidence**

```
== TC-PC-026 — company-aud token w/ role=platform_admin + REAL admin sub -> 401 (audience, not role) ==
[control] GET /platform/me (real platform token): 200 -> real admin id 609f2b17-bee9-4f7f-a26d-cb08f666497a
[attack]  GET /platform/me (company-aud, role=platform_admin, sub=real admin): 401
          body: {'detail': 'Access token is invalid.'}
RESULT: PASS — boundary is the AUDIENCE not the role (company token can't self-promote)
```

**Verdict**

The defense held and is discriminating. `get_current_platform_admin` (`dependencies.py:116`)
binds on `decode_access_token(..., PLATFORM_AUDIENCE)` (`security/tokens.py:77-83`), which checks
`aud` only — it never inspects `role`. So a company-aud token (`aud='company'`) fails the audience
verification *before* the role claim is ever read, even when `role` is escalated to
`platform_admin` and the sub is a real active admin. The **audience is the load-bearing boundary**;
the role claim is not a path to cross-domain escalation. CONFIRMS-FIXED (PC-02-AC3a, role-hardened
variant).

**Notes / follow-up**
Discriminating sibling of TC-PC-020 (same real-admin-sub construction; this one adds the escalated
role to isolate the *audience vs role* question). Together they close the test-1 confidence gap on
the `/platform/me` surface.
