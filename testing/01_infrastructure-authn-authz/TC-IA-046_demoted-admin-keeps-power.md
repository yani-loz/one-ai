<!--
  Test-case: TC-IA-046. See ../README.md for legend, tags, severity scale.
-->

# TC-IA-046: Demoted admin keeps admin power until access-token expiry

| Field | Value |
|---|---|
| **ID** | TC-IA-046 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Token lifecycle |
| **Type** | Adversarial |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ❌ Fail |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Characterize the documented "no access-token denylist" gap (`FIX_BEFORE_PROD.md` — *Add
an access-token denylist for immediate revocation*). `require_company_admin` reads `role`
from the stateless JWT and never re-checks the DB (`dependencies.py:90-100`), so a
company-admin demoted to `member` keeps full `/users` authority on their STILL-VALID
access token until it naturally expires (15 min). This is the deliberate contrast to
TC-IA-044/045 (service-backed re-checks). Behaving "as designed-and-deferred" → the
*expected* outcome here is that the stale token STILL WORKS (defense is documented-absent,
not present).

## Break hypothesis
Two readings:
- **Contract-as-deferred (expected):** the demoted admin's old token still GETs/POSTs
  `/users` → proves the documented denylist gap is real and live.
- **Would-be-defect:** if the system DID re-check the DB role per request, the old token
  would start returning `403` immediately after demotion. (Not implemented; we predict it
  does NOT 403.)

## Preconditions
- Live stack; fresh run-stamped org `token-<stamp>` with TWO admins so the demotion does
  not trip the last-admin guard:
  - admin1 = onboarding admin `admin1-<stamp>@token.test`.
  - admin2 = `admin2-<stamp>@token.test`, created by admin1 with role `company_admin`.

## Steps
1. Onboard org → admin1. admin1 logs in → capture admin1 access token AT1.
2. admin1 creates admin2 (role `company_admin`). admin2 logs in → capture AT2.
3. (Control) admin1 `GET /users` with AT1 → expect `200` (admin1 is still admin).
4. admin2 `PATCH /users/{admin1_id}` `{"role":"member"}` with AT2 → expect `200`; verify
   the DB row for admin1 is now `role=member` (psql ground-truth).
5. With the SAME pre-demotion AT1, admin1 `GET /users` → observe status.
6. With the SAME AT1, admin1 `POST /users` (create a throwaway member) → observe status.

## Expected result (documented-deferral contract)
- Step 4: `200` and DB shows admin1 `role=member`.
- Steps 5 & 6: still `200` / `201` — the stateless access token keeps admin power because
  `require_company_admin` trusts the JWT `role` claim and never re-checks the DB. A 403
  would mean an unexpected (undeferred) per-request DB re-check.

## Harness
Script: `harness/tc_046.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_046.py`

---

## Execution result

- **Run at:** 2026-05-31 (local)
- **Result:** ❌ Fail
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> admin2 PATCHed admin1 to `member` (`200`, body `role=member`); a fresh re-login by
> admin1 minted a token whose `role` claim is `member` — proving the DB demotion
> committed. DB ground-truth (psql) confirms admin1 is `role=member, is_active=t`. Yet
> admin1's STALE pre-demotion access token (AT1) STILL returned `200` on `GET /users` and
> `201` on `POST /users` after the demotion. The stateless access token retains
> company-admin authority on the role-gated `/users` surface until it naturally expires.

**Evidence**

```
[setup] namespace=token-19e7d335b48908c ... admin1=admin1-19e7d335b48908c@token.example.com admin2=admin2-19e7d335b48908c@token.example.com
[setup] onboard_org -> 201
[setup] admin1_id=24904757-31e2-40e3-92ab-83c5739e9603
[step1] admin1 login -> 200  AT1 captured (pre-demotion)
[step2] admin1 creates admin2 (company_admin) -> 201  role=company_admin
[step2] admin2 login -> 200  AT2 captured
[step3] CONTROL admin1 GET /users with AT1 -> 200  count=2
[step4] admin2 PATCH admin1 -> member -> 200  new_role=member
[step4b] admin1 re-login fresh token role claim = 'member'  (proves DB now says member)
[step5] admin1 GET /users with STALE pre-demotion AT1 -> 200  count=2
[step6] admin1 POST /users with STALE AT1 -> 201  created_role=member
[verdict] documented-denylist-gap LIVE=True (demoted in DB, yet stale AT1 still GETs 200 & POSTs 201)
```

DB ground-truth (psql), confirming the demotion is committed:

```
$ docker compose exec -T db psql -U oneai -d oneai -c "SELECT id, email, role, is_active FROM users WHERE id = '24904757-31e2-40e3-92ab-83c5739e9603';"
                  id                  |                  email                   |  role  | is_active
--------------------------------------+------------------------------------------+--------+-----------
 24904757-31e2-40e3-92ab-83c5739e9603 | admin1-19e7d335b48908c@token.example.com | member | t
```

**Verdict**

Defense BROKE — recorded as ❌ Fail (the adversarial win), tagged CONFIRMS-DOCUMENTED
because it reproduces a tracked deferral rather than a new gap. (Result = held-vs-broke;
tag = known-vs-new — they are orthogonal. A reproduced known defect is still a Fail, same
as the dashboard's TC-IA-035/036 forged-token cases.) `require_company_admin`
(`backend/app/identity/dependencies.py:90-100`) reads `principal.role` from the verified
JWT claim and never re-queries the DB; `get_current_principal` (`:72-87`) builds the
Principal purely from claims. So a demoted admin's still-valid (≤15-min TTL) access token
keeps full `/users` authority — a real authorization defect, not a side caveat.
**Severity Medium, blast radius:** a single org, bounded to the access-token TTL — a
fired/demoted admin retains read+create over org users for up to 15 minutes; not
cross-tenant. This is the live realization of the tracked `docs/FIX_BEFORE_PROD.md` item
*"Add an access-token denylist for immediate revocation"* (check a `jti` denylist in
`get_current_principal`). Contrast TC-IA-044/045 where service-resolved paths DO re-check
`is_active` and the defense holds.

**Notes / follow-up**

Remediation is the tracked denylist: on demotion/deactivation/logout, add the affected
`jti`(s) to a fast store (Redis) and check it in `get_current_principal` /
`get_current_platform_admin`. Until then, the 15-minute access-token TTL is the only bound
on stale-privilege window. Pairs with TC-IA-044/045 to fully map the re-check boundary.
