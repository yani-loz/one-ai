<!--
  XDOM suite — cross-domain confinement + forged-token blast radius on the new write
  endpoints. See ../README.md (testing/README.md) for legend/tags.
-->

# TC-OL-022: Company-aud token (real admin sub) rejected on `PATCH …/legal-hold` — 401 + no write

| Field | Value |
|---|---|
| **ID** | TC-OL-022 |
| **Target** | Org Lifecycle (PC-03a) — `PATCH /platform/orgs/{id}/legal-hold` |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial (DISCRIMINATING) |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A company-audience token (REAL platform-admin `sub`) must be rejected (401) on the **legal-hold**
write endpoint — the most safety/compliance-critical flag — AND the rejected PATCH must leave
`legal_hold` UNCHANGED (`false`), confirmed via an independent real-platform read-back. The
company domain cannot set a compliance legal hold on any org.

## Break hypothesis
Legal hold is the safety-critical flag (sec-2 in the PR-3a review flagged this very endpoint as
needing a dedicated company-token-rejection test). If the audience guard were missing, the forged
company token would reach `set_legal_hold`, flip `legal_hold` to `true`, and the session would
commit — letting a company-side actor place (or, by the same path, clear) a legal hold. The
defense bet: `get_current_platform_admin` 401s the company-aud token at the dependency layer
before the service runs; the read-back proves no write occurred.

## Preconditions
- Live stack up; demo platform admin exists/active; real admin id fetched at runtime.
- A run-stamped, freshly-provisioned target org (legal_hold `false`) — OURS, safe to target.
- `forge_company_token(sub=<real admin id>, org_id=None)` → hostile company-aud token.
- PATCH uses the `patch_legal_hold` helper (valid body `{"legal_hold":true}`), so the 401 is
  unambiguously the audience gate — not a 422.

## Steps
1. `platform_login` + `GET /platform/me` → real admin id; `provision_company` → target org.
2. Forge a company-aud token with `sub=<real admin id>`.
3. `PATCH /platform/orgs/{id}/legal-hold {"legal_hold":true}` with the forged company token.
4. `GET /platform/orgs/{id}` with the REAL platform token → read back `legal_hold`.

## Expected result
- Step 3: `401 {"detail":"Access token is invalid."}` (audience mismatch).
- Step 4: `legal_hold == false` — UNCHANGED. The rejected PATCH committed nothing.

## Harness
Script: `harness/tc_022.py` · run: `cat testing/05_org-lifecycle/harness/_common.py testing/05_org-lifecycle/harness/tc_022.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The forged company-aud token (real admin sub) was rejected at
> `PATCH /platform/orgs/{id}/legal-hold` with `401 {"detail":"Access token is invalid."}`. The
> independent read-back with the real platform token shows `legal_hold` is still `false` — the
> rejected PATCH committed nothing. psql ground-truth corroborates (`legal_hold=f`).

**Evidence**

```
== TC-OL-022 — company-aud token (real admin sub) -> PATCH legal-hold, 401 + NO write ==
[setup]   target org: xdom-19e8356b464ecd9 (acbb6bd6-2853-4e91-9003-be3f98e9fa27) legal_hold_before=False
[attack]  PATCH /legal-hold {true} (FORGED company-aud token): 401
          body: {'detail': 'Access token is invalid.'}
[readback] GET detail (real platform token): 200 legal_hold_after=False
RESULT: PASS — 401 at auth dependency; legal_hold unchanged (false) => no write

-- psql ground-truth --
         slug         | legal_hold
----------------------+------------
 xdom-19e8356b464ecd9 | f
(1 row)
```

**Verdict**

The defense held on the safety-critical flag. The 401 fires at the route's
`get_current_platform_admin` dependency
(`backend/app/identity/routes/platform_routes.py:158` → `dependencies.py:117`/`security/tokens.py:77-83`)
*before* `update_organization_legal_hold` or `PlatformOrgService.set_legal_hold`
(`services/platform_org_service.py:58`) ever runs — so `legal_hold` is never assigned and the
unit-of-work never commits. Valid body (`{"legal_hold":true}` via `patch_legal_hold`) means the
401 is the audience gate, not a 422. This is the live, end-to-end confirmation of the PR-3a
review's sec-2 hardening (a dedicated company-token-rejection test on the most compliance-critical
endpoint). CONFIRMS-FIXED (PC-03a-AC6, legal-hold + no-side-effect).

**Notes / follow-up**

Target org left with `legal_hold=false` (no net change). Twin of TC-OL-020/021. Contrast
TC-OL-025, where a *platform*-aud forged token DOES set the legal hold — isolating audience as the
control the company side fails.
