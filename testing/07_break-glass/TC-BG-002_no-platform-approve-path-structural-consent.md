<!--
  Test-case template. Copy this file to testing/<NN>_<target>/TC-<TT>-<NNN>_<slug>.md
  and fill every section. Author the top half BEFORE running; write the
  "Execution result" block back into this same file AFTER running.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-002: No platform path can produce `approved` — consent is structural

| Field | Value |
|---|---|
| **ID** | TC-BG-002 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | CONSENT — approval path + forged-token blast radius |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
⭐ PC-05-AC2: there is NO platform-side approve path. The platform service exposes only
`request` / `list-mine` / `revoke`; the ONLY way a grant reaches `status='approved'` is the
company endpoint `POST /support-access/{id}/approve`. Consent is structural, not a flag.

## Break hypothesis
A platform admin can self-approve their own request — either a hidden `/platform/.../approve`
route exists, or some platform endpoint flips status to `approved`. If so, Ethera staff could
grant themselves customer access with no customer in the loop, defeating break-glass entirely.

## Preconditions
- Live stack `:8000`. Demo platform admin token (real), real company_admin token (from a
  fresh provisioned org for the contrast probe).
- Run-stamped namespace: `provision_company(prefix="consent-bg002")`.
- DYNAMIC evidence required (no static-only verdict): enumerate the live OpenAPI routes and
  probe a plausible platform approve URL against the running server.

## Steps
1. Platform login; `provision_company` a fresh org; `request_support` to get a grant id.
2. `GET /openapi.json`; enumerate every path containing `approve` and every `/platform/*`
   path → show the ONLY approve route is company-side `/support-access/{grant_id}/approve`,
   and no `/platform/.../approve` exists.
3. Adversarial probe: `POST /platform/support-requests/{grant_id}/approve` with the REAL
   platform token → expect `404` (route does not exist) or `405`.
4. Positive control: the company `POST /support-access/{grant_id}/approve` with the real
   company_admin token IS the path that reaches `approved` (proves approval is reachable —
   just not from the platform side).
5. Code anchor: `PlatformSupportService` exposes only `request_access` / `list_my_requests`
   / `revoke` — no approve method (`platform_support_service.py`).

## Expected result
- OpenAPI: exactly one approve path, `/support-access/{grant_id}/approve`; zero
  `/platform/*` approve paths.
- `POST /platform/support-requests/{id}/approve` → `404` (or `405`).
- Company approve → `200`, `status='approved'` (positive control).

## Harness
Script: `harness/tc_002.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_002.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 18:13 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> The live OpenAPI spec exposes exactly one `approve` route — the company-side
> `/support-access/{grant_id}/approve`. No `/platform/*` path contains `approve`. Probing the
> non-existent `POST /platform/support-requests/{id}/approve` with the real platform token
> returned `404`. The company approve endpoint (real company_admin) reaches `approved=200`.

**Evidence**

```
== ORG == b8f8f800-9787-485c-b332-96bd5bceee4a consent-bg002-19e8463b0ebfd42
== REQUEST status == 201 grant 56a9a198-c67e-40fc-8d20-18107ab4348d
== APPROVE ROUTES (live OpenAPI) == ['/support-access/{grant_id}/approve']
== /platform/* ROUTES ==
    /platform/audit ['get']
    /platform/login ['post']
    /platform/logout ['post']
    /platform/me ['get']
    /platform/orgs ['get', 'post']
    /platform/orgs/{org_id} ['get']
    /platform/orgs/{org_id}/audit ['get']
    /platform/orgs/{org_id}/compliance-export ['get']
    /platform/orgs/{org_id}/erase ['post']
    /platform/orgs/{org_id}/legal-hold ['patch']
    /platform/orgs/{org_id}/status ['patch']
    /platform/orgs/{org_id}/support-requests ['post']
    /platform/refresh ['post']
    /platform/support-requests ['get']
    /platform/support-requests/{grant_id}/revoke ['post']
== /platform/* containing 'approve' == []
== PROBE POST /platform/support-requests/{id}/approve == 404
== COMPANY approve (real admin) == 200 approved
```

**Verdict**

Consent is structural. The platform router has no approve endpoint and the
`PlatformSupportService` has no approve method (`platform_support_service.py:60-105` — only
`request_access`, `list_my_requests`, `revoke`); the sole approval path is
`company_support_service.py:70` (`approve`), reachable only via the company router gated by
`require_company_admin` (`support_routes.py:96-103`). A platform admin cannot self-approve.
Defense held. (Pure contract test — no prior fix maps; tag —.) NOTE: this structural barrier
is bypassed by a FORGED company_admin token, characterized separately in TC-BG-003.

**Notes / follow-up**

The structural consent barrier rests entirely on the JWT audience + the dev-secret secrecy —
see TC-BG-003 for the forged-token blast radius (CONFIRMS_DOCUMENTED, Rotate JWT_SECRET).
