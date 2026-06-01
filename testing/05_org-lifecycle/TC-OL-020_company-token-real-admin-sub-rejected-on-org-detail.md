<!--
  XDOM suite — cross-domain confinement + forged-token blast radius on the new write
  endpoints. Author top half BEFORE running; write the Execution result block back AFTER
  running. See ../README.md (testing/README.md) for legend/tags.
-->

# TC-OL-020: Company-aud token with a REAL platform-admin sub is rejected on `GET /platform/orgs/{id}`

| Field | Value |
|---|---|
| **ID** | TC-OL-020 |
| **Target** | Org Lifecycle (PC-03a) — `/platform/orgs/{id}` |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial (DISCRIMINATING) |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-03a-AC6 **discriminatingly** on the new detail endpoint: a company-audience access
token whose `sub` is the REAL demo platform admin's id must be rejected (401) at
`GET /platform/orgs/{id}`. The audience check (`decode_access_token(..., PLATFORM_AUDIENCE)`)
must be the **sole** reason for the 401 — `sub` resolves to a real, active platform admin, the
signature is a valid `DEV_SECRET` HS256 signature, and the token is unexpired, so removing the
audience guard would flip this to 200 (the detail of the org would leak to a company-side token).

## Break hypothesis
If `get_current_platform_admin` did not pin the audience to `platform`, a company token carrying
a real admin id would sail through `decode_access_token` (same HS256 dev secret), build a valid
Principal, and `get_detail` would return the org's metadata — **200 with `{id,name,slug,status,
user_count,legal_hold,created_at}`**. A random-sub company token would not discriminate here:
the platform gate does no admin-existence check (it audience-checks only), so a random sub would
also 401 via the *same* audience path — but using a REAL admin sub removes every alternative
failure mode, leaving the audience guard as the only surviving cause.

## Preconditions
- Live stack up; demo platform admin `super@ethera.ai` exists and is active.
- The real admin id is fetched at runtime (`/platform/login` → `GET /platform/me`) — never
  hardcoded, so it survives a reseed.
- A run-stamped target org is provisioned via `provision_company(c, plat, "xdom")` so a hostile
  200 would be on a known, namespaced org id (and detectable).
- `forge_company_token(sub=<real admin id>, org_id=None)` mints the hostile token (aud='company',
  role='company_admin', dev secret). `org_id=None` is cosmetic — decode fails on audience first.

## Steps
1. `platform_login_pair` → real platform access token.
2. `GET /platform/me` with the real token → capture `id` (the real admin id).
3. `provision_company` → a run-stamped target org id (control: a real platform GET on it = 200).
4. Forge a **company-aud** token with `sub = <that real admin id>`.
5. `GET /platform/orgs/{target_org_id}` with the forged company token.

## Expected result
- Step 3 control: `GET /platform/orgs/{id}` with the real platform token → `200` (org exists).
- Step 5: `401 {"detail":"Access token is invalid."}` (audience mismatch), **never** 200, never
  a not-found body, never 500. The rejection fires in `decode_access_token` BEFORE
  `_principal_from_claims` ever reads `sub`/`role` — so audience is provably load-bearing.

## Harness
Script: `harness/tc_020.py` · run: `cat testing/05_org-lifecycle/harness/_common.py testing/05_org-lifecycle/harness/tc_020.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The real platform token returned the org's metadata (control proving the org id is live and
> the same id is used in the attack). The forged **company-aud** token carrying the REAL platform
> admin's id was rejected at `GET /platform/orgs/{id}` with `401 {"detail":"Access token is
> invalid."}`. Because `sub` resolves to a real, active admin, the signature is a valid
> `DEV_SECRET` HS256 signature, and the token is unexpired, the audience guard is the only
> surviving cause of the 401 — removing it would yield 200 and leak the org detail. Discrimination
> confirmed.

**Evidence**

```
== TC-OL-020 — company-aud token w/ REAL admin sub -> GET /platform/orgs/{id} (DISCRIMINATING) ==
[control] GET /platform/me (real platform token): 200
          real admin id: 7631866b-77e8-4411-8530-bc0cddfa1e28
[setup]   provisioned target org: xdom-19e8355174eb938 (9bbb3f7c-9ba1-4aef-aafb-e7de0b9ab899)
[control] GET /platform/orgs/{id} (real platform token): 200 fields=['created_at', 'id', 'legal_hold', 'name', 'slug', 'status', 'user_count']
[attack]  GET /platform/orgs/{id} (FORGED company-aud token, sub=real admin id): 401
          body: {'detail': 'Access token is invalid.'}
RESULT: PASS — audience guard is load-bearing (401 is audience, not not-found/role)
```

**Verdict**

The defense held. The audience check in `get_current_platform_admin`
(`backend/app/identity/dependencies.py:117` → `decode_access_token(credentials.credentials,
PLATFORM_AUDIENCE)`) is the **load-bearing** boundary, implemented by PyJWT's `audience=`
verification in `security/tokens.py:77-83`. The rejection fires inside `decode_access_token`
BEFORE `_principal_from_claims` (dependencies.py:118) ever reads `sub`/`role`/`org_id` — so with
a real-admin sub the only failure mode left is the audience mismatch. The platform gate does no
admin-existence check, so this construction is precisely what isolates *audience* as the control
(a random sub would 401 via the same audience path and prove nothing). CONFIRMS-FIXED (PC-03a-AC6,
discriminating). Pairs with TC-PC-020 (same construction on `GET /platform/me`).

**Notes / follow-up**

No data mutated; the real demo admin was only read, and the run-stamped target org is left
active. Twin of TC-OL-021/022 (same forged company token on the two PATCH write endpoints).
