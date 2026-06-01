# TC-OL-005: Full suspension blast radius — the access-token path is ungated (≤TTL cutoff)

| Field | Value |
|---|---|
| **ID** | TC-OL-005 · **Target** Org Lifecycle (PC-03a) · **Suite** SUSPEND ⭐ |
| **Type** | Adversarial · **Severity if fail** Medium · **Status** Executed |
| **Result** | ⚠️ Pass-with-concern · **Finding tag** CONFIRMS-DOCUMENTED (no access-token denylist) |

## Objective
Measure how *complete* a suspension is. Suspension blocks **new** sessions (login) and **refresh**, but the
company **access-token** path re-checks nothing — so a pre-suspension access token reaches `/users` (and
every other company endpoint) until it expires.

## Break hypothesis
An operator suspends a company expecting an immediate cutoff. In reality a user who already holds a fresh
access token keeps full company access (read users, etc.) for up to the access-token TTL (~15 min), because
no endpoint on the access path re-validates org status and there is no access-token denylist.

## Steps / Harness
`provision_company("sus005")` → suspend → `GET /users` with the pre-suspension company-admin access token;
also a fresh login (to contrast). `harness/_finish_suspend.py` (case 005).

## Execution result
- **Run at:** 2026-06-01 local · **Result:** ⚠️ Pass-with-concern · **Tag:** CONFIRMS-DOCUMENTED

**Evidence**
```
[005] /users (pre-susp admin access) under suspension: 200 (expect 200);
      NEW login under suspension: 403 (expect 403)
      => immediate for new sessions, eventual (<=access TTL) for in-flight
```

**Verdict**
Behaviour is correct per the design (S4 scopes the gate to "logins and refreshes"), but the **operator-facing
meaning of "suspended" is "≤15 min to full cutoff," not "instant."** A suspended org's admin keeps reading
`/users` with an in-flight token until it expires; they just can't refresh past expiry. Root: the access path
(`get_current_principal`/`get_tenant_session`/`require_company_admin`) never re-checks `organizations.status`
— only login + refresh do — and access tokens are stateless with no denylist. This is the **tracked**
*Add an access-token denylist for immediate revocation* item in `FIX_BEFORE_PROD.md`; closing it would make
suspension immediate. CONFIRMS-DOCUMENTED, not a new defect.

**Notes** The complement of TC-OL-004; bounds the suspend-vs-login race window in TC-OL-060.
