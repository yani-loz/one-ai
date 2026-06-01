<!--
  Test-case template. Copy this file to testing/<NN>_<target>/TC-<TT>-<NNN>_<slug>.md
  and fill every section. Author the top half BEFORE running; write the
  "Execution result" block back into this same file AFTER running.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-003: A forged dev-secret `company_admin` token manufactures the customer's consent

| Field | Value |
|---|---|
| **ID** | TC-BG-003 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | CONSENT — approval path + forged-token blast radius |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Characterize the blast radius of the forgeable dev JWT secret against the consent gate.
Break-glass promises "access only if the customer says yes." That promise rests on the
company_admin's token, which under the **dev default secret** (`DEV_SECRET`) anyone can
forge. A forged `company_admin` token with `org_id=<target>` should be able to call the
company approve endpoint and **manufacture the consent** the system structurally requires.

## Break hypothesis
The consent gate (TC-BG-002 proved no platform self-approve path) is defeated by forging a
company_admin token: the approve endpoint verifies only the JWT signature + audience + role +
`org_id` claim, all of which the dev secret lets an attacker mint freely. So Ethera (or anyone
holding the leaked secret) can self-consent into ANY tenant — exactly what break-glass forbids.

## Preconditions
- Live stack `:8000`. RLS is **inert** (app connects as superuser `oneai`), so the JWT secret
  is the single isolation layer; the dev default is the forgeable `DEV_SECRET`.
- Run-stamped namespace: `provision_company(prefix="consent-bg003")` — the target org is MINE
  (safe to drive into `approved`). Never act on demo/globex.
- This reproduces the SINGLE tracked "Rotate `JWT_SECRET`" + inert-RLS deferral
  (`docs/FIX_BEFORE_PROD.md` — Auth hardening + Tenant isolation). Tag CONFIRMS-DOCUMENTED.

## Steps
1. Platform login; `provision_company` a fresh org; `request_support` → grant id, status
   `requested`.
2. `forge_company_token(sub=str(uuid4()), org_id=<my org>, role='company_admin')` — a random
   `sub` (matches NO real user), signed with `DEV_SECRET`.
3. `POST /support-access/{grant}/approve` with the FORGED token.
4. psql ground-truth: read the persisted row — confirm `status='approved'`, `expires_at` set,
   and `decided_by_email IS NULL` (the random `sub` resolves to no user).
5. `get_org_audit` (platform token) → confirm a `support.approved` event was logged with the
   phantom actor.

## Expected result
- `POST /support-access/{grant}/approve` (forged) → **`200`**, `status='approved'`,
  `is_active=true`, `expires_at ≈ now+4h`.
- `decided_by_email=null` (the forged `sub` matches no user — attribution is a phantom).
- psql: `status='approved'`, `expires_at` non-null, `decided_by_email` NULL,
  `decided_by_user_id` = the forged random UUID.
- audit_log has a `support.approved` row attributed to the non-existent actor.

## Harness
Script: `harness/tc_003.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_003.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 18:13 local
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> The forged `company_admin` token (random `sub`, `org_id=<my org>`, signed with the dev
> secret) was accepted on `POST /support-access/{grant}/approve` → `200`, `status='approved'`,
> `is_active=true`, `expires_at` set ~4h out. As predicted, `decided_by_email=null` because the
> phantom `sub` resolves to no user. psql confirmed the persisted `approved` row + a non-null
> `decided_by_user_id` equal to the forged UUID; the audit_log carries a `support.approved`
> event attributed to that phantom actor. The forged token manufactured the customer's consent.

**Evidence**

```
== ORG == 0fdb798b-b040-4274-bcef-1c8f7f928179 consent-bg003-19e8463fb25d742
== REQUEST == 201 grant 488d5c97-eed0-4d1b-8fdf-2d857984880a status requested
== FORGED token sub (random, no user) == a63d510f-a5b1-44f9-a349-07f27802b957
== FORGED approve status == 200
status      : approved
is_active   : True
expires_at  : 2026-06-01T22:13:26.067344Z
decided_at  : 2026-06-01T18:13:26.067344Z
decided_by  : None
== FULL BODY == {'id': '488d5c97-eed0-4d1b-8fdf-2d857984880a', 'org_id': '0fdb798b-b040-4274-bcef-1c8f7f928179', 'requested_by_admin_id': '21760a63-7466-458d-b60b-01bf49c88c44', 'requested_by_email': 'super@ethera.ai', 'reason': 'break-glass: incident investigation', 'status': 'approved', 'is_active': True, 'decided_at': '2026-06-01T18:13:26.067344Z', 'decided_by_email': None, 'expires_at': '2026-06-01T22:13:26.067344Z', 'created_at': '2026-06-01T18:13:26.052886Z'}
== AUDIT support.* events == [{'action': 'support.approved', 'actor_id': 'a63d510f-a5b1-44f9-a349-07f27802b957', 'actor_email': None}, {'action': 'support.requested', 'actor_id': '21760a63-7466-458d-b60b-01bf49c88c44', 'actor_email': None}]

-- psql ground-truth (db container) --
SELECT status, expires_at, decided_at, decided_by_email, decided_by_user_id
  FROM support_grant WHERE id = '488d5c97-eed0-4d1b-8fdf-2d857984880a';

  status  |          expires_at           |          decided_at           | decided_by_email |          decided_by_user_id
----------+-------------------------------+-------------------------------+------------------+--------------------------------------
 approved | 2026-06-01 22:13:26.067344+00 | 2026-06-01 18:13:26.067344+00 |                  | a63d510f-a5b1-44f9-a349-07f27802b957
(1 row)
```

**Verdict**

⚠️ The consent gate held against the *structural* attack (TC-BG-002) but is fully defeated by
a forged token. **Severity: Critical.** **Blast radius:** with the dev JWT secret
(`DEV_SECRET`), an attacker mints a `company_admin` token for ANY `org_id` and self-approves a
break-glass grant — manufacturing the exact customer consent break-glass exists to guarantee
("access only if we say yes"). The approval requires no real user (random `sub` → 200), and the
phantom decider (`decided_by_email=null`) means the trail can't even attribute the consent to a
real person, so the manufactured approval is also unaccountable. **Root cause:** the dev default
JWT secret is forgeable AND RLS is inert (app connects as superuser), so the JWT signature is the
SINGLE isolation layer — `dependencies.get_current_principal` (`dependencies.py:79-94`) trusts any
correctly-signed token; the org_id comes straight from the claim. This is the SAME root as the
tracked "Rotate `JWT_SECRET`" + "Enforce RLS" items in `docs/FIX_BEFORE_PROD.md`. CONFIRMS-DOCUMENTED
(not a NEW defect). Result PASS_WITH_CONCERN rather than FAIL: it reproduces a documented,
accepted deferral, not a newly-discovered contract break.

**Notes / follow-up**

Remediation = `FIX_BEFORE_PROD.md` "Rotate `JWT_SECRET`" (fail boot on the dev default in prod)
+ "Enforce the (already-defined) Postgres RLS policy" (connect as a non-superuser role so a
forged org_id claim still can't read/write another tenant's rows). The phantom-attribution
detail (`decided_by_email=null`) also argues for binding `decided_by_user_id` to an existing
user. Cross-ref: TC-BG-002 (structural barrier), TC-BG-004 (a REAL approve's attribution).
