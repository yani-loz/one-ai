<!--
  Test-case template. Copy this file to testing/<NN>_<target>/TC-<TT>-<NNN>_<slug>.md
  and fill every section. Author the top half BEFORE running; write the
  "Execution result" block back into this same file AFTER running.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-ER-031: A real company_admin token cannot self-erase or self-export its own org via the platform endpoints (401)

| Field | Value |
|---|---|
| **ID** | TC-ER-031 |
| **Target** | GDPR erasure + compliance export (PC-06) |
| **Suite** | AUTHZ — audience confinement + forged-token blast radius |
| **Type** | Negative |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove that erasure/export is platform-only and a tenant cannot self-serve it: a **genuine** company_admin
access token (issued by `provision_company` login) presented to `POST /platform/orgs/{own org}/erase` and
`GET .../compliance-export` is rejected with **401** (the platform gate decodes against `aud='platform'`,
and a real company token carries `aud='company'`).

## Break hypothesis
If the platform endpoints accepted a company-audience token (e.g. a shared decoder that ignored audience,
or a role check that ran before the audience check and let `company_admin`-as-self through), a tenant could
irreversibly erase its own org or pull its full audit trail without platform involvement — bypassing the
offboarding control. The bet: the gate confuses "valid company token" with "authorized platform caller."

## Preconditions
- Live stack `:8000`. Suite code **AUTHZ**, run-stamped slug (lowercase `[a-z0-9-]`).
- Onboard ONE fresh AUTHZ org via `provision_company`; use its returned `admin_access` (a real,
  signature-valid, unexpired company_admin token for THAT org).
- The org under test must remain untouched (active, users intact).

## Steps
1. `provision_company("authz-self-…")` → `{org_id, slug, admin_access}` (real company_admin token).
2. `POST /platform/orgs/{own org}/erase` with `admin_access` (+ correct slug) → expect 401.
3. `GET /platform/orgs/{own org}/compliance-export` with `admin_access` → expect 401.
4. psql ground-truth: org still `active`, users intact, no `org.erased` row.

## Expected result
- Both → **401** (NOT 403): the platform gate fails at audience-decode in
  `get_current_platform_admin`/`decode_access_token` BEFORE any role logic — `require_company_admin` is
  not even on the platform path, so there is no 403 to reach.
- psql: org `status='active'`, user count unchanged, zero `org.erased` audit rows.

## Harness
Script: `harness/tc_031.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_031.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 18:43 local (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The genuine company_admin token for the org was rejected with **401** ("Access token is invalid.") by
> both the erase and the compliance-export platform endpoints. The rejection is 401, not 403 — the
> audience-decode fails before any role gate runs. psql confirms the org stayed `active` with its admin
> intact and zero `org.erased` rows.

**Evidence**

```
# harness stdout
ORG authz-self-19e847f9800e525 25f42046-92d9-4e1f-bf98-30f98b730529
ERASE_STATUS 401
ERASE_BODY {"detail":"Access token is invalid."}
EXPORT_STATUS 401
EXPORT_BODY {"detail":"Access token is invalid."}

# psql ground-truth (db container)
           slug            | status | users | erased_rows
---------------------------+--------+-------+-------------
 authz-self-19e847f9800e525 | active |     1 |           0
```

**Verdict**

Defense **held**. A real, fully-valid company_admin token is rejected at the platform audience gate
(`decode_access_token(..., audience='platform')`, `tokens.py:77-83`, invoked from
`get_current_platform_admin`, `dependencies.py:123`). The 401 (not 403) confirms the gate is an
*audience* boundary, not a role boundary on a shared session — `require_company_admin`
(`dependencies.py:97`) is not on this route. The tenant therefore cannot self-erase or self-export via
the platform surface; erasure remains platform-initiated offboarding only. (Corroborating the
audience-first ordering: the body also lacks the now-required `password`, yet the response is 401 "token
invalid" — not 422 — so the audience decode rejects before body validation.) Confirms PC-06-AC6.

**Notes / follow-up**

Pairs with TC-ER-030 (forged company-aud token with the real admin sub — same 401). The complement is
TC-ER-032/033: a *platform*-aud forged token defeats the audience gate; erase is then still blocked by
the (undocumented) sudo password re-auth (403), export is not (200). The audience firewall's strength is
exactly JWT-secret secrecy (tracked: "Rotate JWT_SECRET", `docs/FIX_BEFORE_PROD.md`).
