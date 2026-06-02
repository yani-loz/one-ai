<!--
  Test-case template. Copy this file to testing/<NN>_<target>/TC-<TT>-<NNN>_<slug>.md
  and fill every section. Author the top half BEFORE running; write the
  "Execution result" block back into this same file AFTER running.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-ER-030: Audience confinement — a company-aud token bearing the real platform-admin sub is rejected (401) by both erasure endpoints

| Field | Value |
|---|---|
| **ID** | TC-ER-030 |
| **Target** | GDPR erasure + compliance export (PC-06) |
| **Suite** | AUTHZ — audience confinement + forged-token blast radius |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-06-AC6 audience confinement is the load-bearing guard on the platform-only erasure
surface: a token whose **signature and expiry are both valid** but whose `aud='company'` must be
rejected (401) by `POST /platform/orgs/{id}/erase` AND `GET .../compliance-export`, even when the
token carries the **real demo platform admin's `sub`**. The guard must rest on the audience claim,
not on "is this sub a known admin."

## Break hypothesis
If `get_current_platform_admin` resolved the principal from `sub` (a known platform-admin id) before
(or instead of) enforcing `aud='platform'`, then a company-aud token minted with the real admin's id
and the genuine dev secret would authenticate as that admin and the irreversible erase would proceed —
catastrophic cross-domain privilege confusion. The attacker's bet: audience is checked too late or not
at all, so a valid-signature company token impersonates a platform admin.

## Preconditions
- Live stack `:8000`. Suite code **AUTHZ**, run-stamped slug (lowercase `[a-z0-9-]`).
- Fetch the demo platform admin's real id via `GET /platform/me` (do NOT mutate it).
- Onboard ONE fresh AUTHZ org via `provision_company` (the org under test — it must stay untouched).
- Forge a **company-aud** token: real dev secret (`DEV_SECRET`), valid (non-expired) exp,
  `sub=<real platform admin id>`, `role=platform_admin`, `org_id=<our fresh org>` (value irrelevant —
  the request is rejected at the auth dependency before the service reads it).

## Steps
1. Platform-login the demo admin; `GET /platform/me` → capture real admin `id`.
2. `provision_company("authz-aud-…")` → fresh org `{org_id, slug}`.
3. Forge a company-aud token (`forge_company_token`, real secret, valid exp, `sub=admin id`).
4. `POST /platform/orgs/{our org}/erase` with the forged company token (+ correct slug) → expect 401.
5. `GET /platform/orgs/{our org}/compliance-export` with the forged company token → expect 401.
6. psql ground-truth: the org row is still `active`; its users are intact (count > 0).

## Expected result
- Both requests → **401** (audience guard fires in `decode_access_token` — `aud='company'` fails the
  `audience='platform'` verification before any service logic).
- psql: org `status='active'`, user count unchanged (≥1 admin user) — nothing touched.

## Harness
Script: `harness/tc_030.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_030.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 18:43 local (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The forged company-aud token — carrying the real demo platform admin's `sub`, a valid signature
> (genuine dev secret) and a non-expired `exp` — was rejected with **401** ("Access token is invalid.")
> by BOTH the erase and the compliance-export endpoints. Because signature and expiry both verify, the
> sole failing check is the audience guard. psql confirms the targeted org stayed `active` with its admin
> user intact and zero `org.erased` rows: nothing was erased or read.

**Evidence**

```
# harness stdout
REAL_PLATFORM_ADMIN_ID 2b940f53-428a-4a76-8a7a-2c27b4983963
ORG authz-aud-19e847f8f827fa5 2e655951-c253-4d9f-8d50-053963250b47
ERASE_STATUS 401
ERASE_BODY {"detail":"Access token is invalid."}
EXPORT_STATUS 401
EXPORT_BODY {"detail":"Access token is invalid."}

# psql ground-truth (db container)
          slug            | status | users | erased_rows
--------------------------+--------+-------+-------------
 authz-aud-19e847f8f827fa5 | active |     1 |           0
```

**Verdict**

Defense **held**. The audience guard is load-bearing and is enforced inside `decode_access_token`
(`backend/app/identity/security/tokens.py:77-83`, `audience='platform'` passed by
`get_current_platform_admin`, `backend/app/identity/dependencies.py:123`). The token's signature was
genuine (dev secret) and unexpired, so PyJWT's `InvalidAudienceError` — surfaced as `TokenInvalidError`
→ 401 — is the *only* possible cause of the rejection. This proves the platform gate confines by
audience, not by trusting a known `sub`. The 401 fires at the auth dependency BEFORE FastAPI body
validation, so the (now password-requiring) `ErasureRequest` schema is never even reached. Corroborating
this ordering: the shared `erase_org()` helper omits the now-required `password`, so this request body is
ALSO invalid — yet the response is 401 "Access token is invalid." (not 422 "password required"), proving
the audience guard fires first; contrast TC-ER-032, where a *valid* platform token with no password yields
422. Confirms PC-06-AC6 holds under a realistic cross-domain forgery (not merely a wrong-password company
token). Both endpoints reject identically.

**Notes / follow-up**

Companion to TC-ER-031 (real company_admin token → 401). The contrast with TC-ER-032/033 is the headline:
a *platform*-aud forged token defeats the audience gate, but erase is then ALSO blocked by an
(undocumented) sudo password re-auth (403), whereas export is not (200). Audience is the firewall between
the two auth domains; its strength is bounded by JWT-secret secrecy (tracked: "Rotate JWT_SECRET",
`docs/FIX_BEFORE_PROD.md`).
