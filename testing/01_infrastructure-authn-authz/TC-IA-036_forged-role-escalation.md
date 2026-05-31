# TC-IA-036: A member can forge a company_admin token (same sub/org) and escalate

| Field | Value |
|---|---|
| **ID** | TC-IA-036 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Cross-tenant isolation (hardest rule) |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ❌ Fail |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Prove that the `role` claim is unverified beyond the JWT signature: a **real member** of
org A — correctly blocked from `/users` with their genuine token — can forge a token with
the **same `sub` and `org_id`** but `role='company_admin'` (using the dev secret) and
thereby pass `require_company_admin`, gaining admin read/write over org A. Same root cause
as TC-IA-035 (forgeable secret), isolated to the role-gate.

## Break hypothesis
The member's genuine token → `403` on `GET /users` (member blocked). Forging the same
identity with `role='company_admin'` under `DEV_SECRET` → `200` on `GET /users` and `201`
on `POST /users`, because `require_company_admin` reads `principal.role` straight from the
signed claim with no server-side cross-check against the user's stored role.

## Preconditions
- Live stack; suite `tenant` org A with an admin (to create the member) and a real
  **member** (`tenant-a-mem-<stamp>@tenant.oneai`). The member authenticates legitimately
  first to obtain a real token, then forges the escalated one with the dev secret. Demo org
  untouched. Documented root cause: `docs/FIX_BEFORE_PROD.md` → "Rotate `JWT_SECRET`".

## Steps
1. Onboard A; admin creates a real member; member logs in (real token).
2. Baseline: member's **real** token `GET /users` → expect `403`.
3. Forge a token: same `sub` + `org_id` as the member, `role='company_admin'`,
   `secret=DEV_SECRET`.
4. Forged token `GET /users` → expect `200`; `POST /users` → expect `201`.

## Expected result (per the contract this violates)
A robust system would bind role to the verified user record (or sign with a non-forgeable
secret). Documented reality: real-token `403`, forged-admin `200`/`201`. Recorded as a Fail
(Critical) tagged CONFIRMS-DOCUMENTED.

## Harness
Script: `harness/tc_036.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_036.py`

---

## Execution result

- **Run at:** 2026-05-31 11:41 local
- **Result:** ❌ Fail (documented privilege escalation reproduced — Critical)
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> The member's genuine token was correctly rejected from `/users` (`403 "Company
> administrator role required."`). Forging a token with the member's **own** `sub`/`org_id`
> but `role='company_admin'` (dev secret) passed `require_company_admin`: `GET /users`
> returned A's user list (`200`) and `POST /users` created a new user in A (`201`). The role
> gate trusts the signed claim with no record-level check.

**Evidence**

```
Real member created in A: a590d772-9dd8-4eaf-92be-43a6f909ae58 role: member
member real claims: {'sub': 'a590d772-9dd8-4eaf-92be-43a6f909ae58', 'org_id': 'e3f98f37-e97e-41df-b463-2babd8356fd9', 'role': 'member'}
MEMBER real token GET /users status: 403 (403 expected — member blocked)
  body: {"detail":"Company administrator role required."}
forged token: same sub/org as member, role=company_admin
FORGED-ADMIN GET /users status: 200 (200 == escalation)
  emails: ['tenant-a-admin-19e7d31ffdfadd4@tenant.oneai', 'tenant-a-mem-19e7d31ffdfadd4@tenant.oneai']
FORGED-ADMIN POST /users status: 201 body: {"id":"f46ee432-a877-4c22-9489-71aa013ee9a5","email":"tenant-a-escalated-19e7d31ffdfadd4@tenant.oneai","full_name":"Escalated","role":"member",...,"org_id":"e3f98f37-e97e-41df-b463-2babd8356fd9",...}
```

**Verdict**

Defense **broke** — **Critical** privilege escalation. The genuine role check works
(member → 403 via `require_company_admin`, `dependencies.py:90-100`), but `principal.role`
is built directly from the JWT `role` claim (`_principal_from_claims`, `dependencies.py:54-69`)
and the claim is forgeable because `JWT_SECRET` is the dev default — so a member who knows
the secret simply re-signs themselves as admin. No server-side comparison against the
user's *stored* role exists. Same root cause as TC-IA-035 (forgeable secret); this isolates
it to the authorization gate. Confirms the documented `FIX_BEFORE_PROD.md` "Rotate
`JWT_SECRET`" item — escalation, not new.

**Notes / follow-up**

Rotating the secret (+ boot-fail on the dev default in prod) closes the forgery. A
stronger belt-and-suspenders option for sensitive gates: resolve the caller's role from the
DB record at request time rather than trusting the claim (the access-token denylist /
record-of-truth work in `FIX_BEFORE_PROD.md` is the natural home). Note the forged
`org_id` still binds to the member's own org A here; combined with TC-IA-035 the same
technique reaches any org.
