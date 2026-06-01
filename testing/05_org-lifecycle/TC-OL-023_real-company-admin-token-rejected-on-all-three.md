<!--
  XDOM suite — cross-domain confinement + forged-token blast radius on the new write
  endpoints. See ../README.md (testing/README.md) for legend/tags.
-->

# TC-OL-023: REAL company_admin token rejected on all three lifecycle endpoints (GET + 2× PATCH)

| Field | Value |
|---|---|
| **ID** | TC-OL-023 |
| **Target** | Org Lifecycle (PC-03a) — `/platform/orgs/{id}` + `…/status` + `…/legal-hold` |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A genuinely-issued company_admin **access token** (from `provision_company`, not forged) must be
rejected (401) on all three new lifecycle endpoints — `GET /platform/orgs/{id}`,
`PATCH …/status`, `PATCH …/legal-hold` — even when the target is the admin's OWN org. The
company side has no reach into the platform lifecycle surface whatsoever (read or write), and the
two rejected PATCHes leave the org unchanged.

## Break hypothesis
This is the confinement corroboration to TC-OL-020/021/022's discriminating cases: the same
audience boundary, but with a *real, server-minted* company token (not a forged one) so there is
no question of the token being malformed. If the platform gate audience-checked weakly (or
role-checked instead of audience-checked), a real company_admin token — which carries
`role=company_admin` and a valid `org_id` — might pass on the admin's own org. The bet: all three
endpoints 401 via `get_current_platform_admin`'s audience pin; the role/org_id claims are
irrelevant because the gate audience-checks, it does not role-check.

## Preconditions
- Live stack up. A run-stamped company is provisioned via `provision_company(c, plat, "xdom")`,
  yielding a REAL company_admin access token and its OWN org id.
- The two PATCHes use the `patch_status`/`patch_legal_hold` helpers (valid bodies), so each 401 is
  the audience gate, not body validation.
- A real platform GET read-back confirms the org is unchanged after the rejected PATCHes.

## Steps
1. `provision_company` → real company_admin access token + own org id.
2. `GET /platform/orgs/{own_org_id}` with the company token.
3. `PATCH …/status {"status":"suspended"}` with the company token.
4. `PATCH …/legal-hold {"legal_hold":true}` with the company token.
5. `GET /platform/orgs/{own_org_id}` with the REAL platform token → confirm status/legal_hold
   unchanged.

## Expected result
- Steps 2–4: each `401 {"detail":"Access token is invalid."}` (audience mismatch).
- Step 5: `status == "active"` AND `legal_hold == false` — UNCHANGED.

## Harness
Script: `harness/tc_023.py` · run: `cat testing/05_org-lifecycle/harness/_common.py testing/05_org-lifecycle/harness/tc_023.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> A real, server-issued company_admin access token was rejected with `401 {"detail":"Access token
> is invalid."}` on all three lifecycle endpoints — GET detail, PATCH status, PATCH legal-hold —
> targeting the admin's OWN org. The real-platform read-back confirms the org is unchanged
> (`status=active`, `legal_hold=False`): neither rejected PATCH had any effect. The company side
> has no reach into the platform lifecycle surface, read or write.

**Evidence**

```
== TC-OL-023 — REAL company_admin token rejected on all three lifecycle endpoints ==
[setup]   own org: xdom-19e8357e11498b2 (b4c92248-f019-4323-91d6-ba671dea89cc) status=active legal_hold=False
          real company_admin access token issued for this org
[attack1] GET /platform/orgs/{own id} (company token): 401 body={'detail': 'Access token is invalid.'}
[attack2] PATCH /status {suspended} (company token): 401 body={'detail': 'Access token is invalid.'}
[attack3] PATCH /legal-hold {true} (company token): 401 body={'detail': 'Access token is invalid.'}
[readback] GET detail (real platform token): 200 status=active legal_hold=False
RESULT: PASS — company side cannot reach platform lifecycle (all 401); org unchanged
```

**Verdict**

Confinement holds with a real token. All three endpoints are gated by `get_current_platform_admin`
(`backend/app/identity/routes/platform_routes.py:136,147,158`), which calls
`decode_access_token(..., PLATFORM_AUDIENCE)` (`dependencies.py:117` → `security/tokens.py:77-83`)
— a `company`-aud token fails the audience check and 401s before any handler/service runs. The
token's `role=company_admin` and valid `org_id` are irrelevant: the platform gate audience-checks,
it does not role-check, so even the admin's own org id grants no access. The two rejected PATCHes
committed nothing (read-back unchanged). This is the confinement corroboration to the
discriminating TC-OL-020/021/022; CONFIRMS-FIXED (PC-03a-AC6). Mirrors TC-PC-023's GET+POST seal
on the older `/platform/orgs` collection endpoints, extended to the PC-03a write surface.

**Notes / follow-up**

Own org left active/unheld (no net change). Completes the company-side seal on all three PC-03a
endpoints; pairs with the platform-token-rejected-on-company-endpoints direction (TC-PC-022) to
close the mutual boundary.
