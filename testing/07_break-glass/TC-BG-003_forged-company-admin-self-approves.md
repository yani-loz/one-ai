# TC-BG-003: A forged dev-secret `company_admin` token manufactures the customer's consent ⭐

| Field | Value |
|---|---|
| **ID** | TC-BG-003 · **Suite** CONSENT · **Type** Adversarial · **Severity if fail** Critical (tracked) |
| **Result** | ⚠️ Pass-with-concern · **Tag** 📋 CONFIRMS-DOCUMENTED · **Status** Executed |

## Objective
Break-glass promises "Ethera staff reach a tenant **only if the customer says yes**." Test whether the
forgeable dev `JWT_SECRET` lets an attacker *manufacture* that consent.

## Execution result (2026-06-01 · lead-verified in the harness probe)
**Evidence**
```
FORGED company_admin token: sub=<random, no user>, org_id=<target>, role=company_admin, dev secret
POST /support-access/{grant}/approve → 200 | status=approved is_active=True expires_at≈+4h decided_by=None
audit support.approved actor_id=<random> actor_email=None | psql: decided_by_email NULL (phantom decider)
```
**Verdict (Critical, documented):** with the forgeable dev secret an attacker mints a `company_admin` token
for ANY `org_id` and self-approves → 200, **manufacturing the customer consent** that break-glass structurally
promises, *unaccountably* (`decided_by_email=null`). Root: dev secret forgeable + RLS inert ⇒ the JWT
signature is the single isolation layer (`get_current_principal`, `dependencies.py:79-94`). Same root as
`FIX_BEFORE_PROD` → *Rotate JWT_SECRET* + *Enforce RLS* — **its most consequential surface.** CONFIRMS-DOCUMENTED,
not NEW; the phantom attribution is a downstream effect of the same root.
