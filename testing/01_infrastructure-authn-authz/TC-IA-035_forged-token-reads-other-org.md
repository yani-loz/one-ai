# TC-IA-035: A forged company token (dev secret) reads and writes another org's users

| Field | Value |
|---|---|
| **ID** | TC-IA-035 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Cross-tenant isolation (hardest rule) |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ❌ Fail |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Prove that tenant isolation has **no second layer** behind the app-layer `org_id` filter:
with the forgeable dev `JWT_SECRET`, an attacker who never held org B's credentials can
mint a `company_admin` token for org B and fully read (and write) B's users. Demonstrates
the combined blast radius of two documented deferrals — the dev JWT secret +
inert (RLS bypassed by the superuser DB connection).

## Break hypothesis
`forge_company_token(sub=<random>, org_id=B_org, role='company_admin')` signed with
`DEV_SECRET` passes signature + aud + expiry verification (the secret is the real default),
so `get_current_principal` builds a Principal with `org_id=B`, and `GET /users` returns B's
rows. The bet: 200 + B's emails + ability to `POST /users` into B. There is no RLS backstop
to stop the org-scoped query from running against B's data.

## Preconditions
- Live stack; suite `tenant` org B with an admin + a member. The attacker has **only** the
  dev secret and B's org id (obtainable from onboarding output or the email oracle), never
  B's credentials. `_common.DEV_SECRET` is the real default. Demo org untouched.
- Documented: `docs/FIX_BEFORE_PROD.md` → "Rotate `JWT_SECRET`" + "Enforce the
  (already-defined) Postgres Row-Level Security policy".

## Steps
1. Onboard org B (admin + member); the attacker learns only B's org id.
2. Forge a company token: `sub=uuid4()`, `org_id=B_org`, `role='company_admin'`,
   `secret=DEV_SECRET`, `aud='company'`.
3. `GET /users` with the forged token.
4. `POST /users` with the forged token (prove write capability too).

## Expected result (per the security contract this is meant to violate)
A robust system would reject a token whose subject is not a real B user / would have a DB
RLS backstop. The documented reality: `200` + B's full user list, and `201` on the
injected create. Recorded as a Fail (Critical) tagged CONFIRMS-DOCUMENTED.

## Harness
Script: `harness/tc_035.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_035.py`

---

## Execution result

- **Run at:** 2026-05-31 11:41 local
- **Result:** ❌ Fail (documented isolation gap reproduced — Critical)
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> A forged `company_admin` token for org B (signed with the dev secret, `sub` = a random
> UUID that is not any real B user) returned `200` with B's complete user list (admin +
> member emails), and a subsequent `POST /users` injected a new member into org B (`201`).
> Full cross-tenant read **and** write with no legitimate B credential.

**Evidence**

```
Target org B: 8c593eb1-e2be-42c7-9715-c68fec45ddf3 | B admin id: cc0e77b8-102a-4ecf-b555-39b105f6d54b
forged token (dev secret, org_id=B, role=company_admin) len: 385
FORGED GET /users status: 200
FORGED GET /users body: [{"id":"46a3a85a-07d1-40d3-b6b1-26af85202e60","email":"tenant-b-mem-19e7d31f6df59a6@tenant.oneai",...,"org_id":"8c593eb1-e2be-42c7-9715-c68fec45ddf3",...},{"id":"cc0e77b8-102a-4ecf-b555-39b105f6d54b","email":"tenant-b-admin-19e7d31f6df59a6@tenant.oneai","full_name":"Org Admin","role":"company_admin",...,"org_id":"8c593eb1-e2be-42c7-9715-c68fec45ddf3",...}]
FORGED token read B emails: ['tenant-b-admin-19e7d31f6df59a6@tenant.oneai', 'tenant-b-mem-19e7d31f6df59a6@tenant.oneai']
FORGED token read org_ids: ['8c593eb1-e2be-42c7-9715-c68fec45ddf3'] == {B}? True
FORGED POST /users status: 201 body: {"id":"653082df-08d7-49bb-872c-557b2aa90c4b","email":"tenant-injected-by-forgery-19e7d31f6df59a6@tenant.oneai",...,"org_id":"8c593eb1-e2be-42c7-9715-c68fec45ddf3",...}
```

**Verdict**

Defense **broke** — **Critical**, full cross-tenant read+write. Blast radius: every org's
data, for anyone holding the (publicly-known) dev secret. Root cause is *by design for the
demo* and documented twice: (1) `JWT_SECRET` is the forgeable dev default
(`backend/app/core/config.py` `jwt_secret`; verification at `security/tokens.py:82`
trusts any HS256 signature under that secret), so the forged token passes
`get_current_principal` (`dependencies.py:84-87`) and `org_id=B` flows into the tenant
session; (2) RLS is **inert** — the app connects as the superuser/owner role `oneai`
which bypasses the `org_isolation` policy (migration `0003`), so there is no DB layer to
catch a token-derived `org_id`. This **confirms** the two `FIX_BEFORE_PROD.md` items
("Rotate `JWT_SECRET`", "Enforce … Row-Level Security"); it is the empirical proof that
the app-layer filter (TC-IA-030/031/032) is the *only* active control.

**Notes / follow-up**

Both controls must land together to fix this: a strong secret + boot-fail on the dev
default in prod removes the forgery; an enforced RLS policy under a least-privilege DB role
adds the missing second layer so a token-claimed `org_id` cannot read another org even if
the signature were ever trusted. Tracked, not new.
