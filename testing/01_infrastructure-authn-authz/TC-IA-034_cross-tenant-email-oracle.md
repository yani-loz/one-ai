# TC-IA-034: Cross-tenant email-existence oracle via 409-vs-201 on POST /users (AUD-04)

| Field | Value |
|---|---|
| **ID** | TC-IA-034 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Cross-tenant isolation (hardest rule) |
| **Type** | Adversarial |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ❌ Fail |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Demonstrate the documented cross-tenant email-existence oracle (AUD-04): because `email`
is globally unique and `email_exists` is unscoped, `POST /users` returns **409** for an
address already registered in *another* tenant vs **201** for a fresh address — letting a
company-admin of A confirm whether a given email belongs to some other org's user. This
contradicts the module's own "never reveal existence in another org" invariant.

## Break hypothesis
Admin A POSTing org B's admin email yields a deterministic `409 "A user with this email
already exists."`, while a fresh/never-registered email yields `201`. The 409-vs-201
distinction is the oracle bit. (A "Pass" here would mean both probes returned the same
status — i.e. no leak — which the code says cannot happen.)

## Preconditions
- Live stack; suite `tenant` orgs A and B (B has an admin whose email A never legitimately
  knows belongs to B). Admin A authenticated. Demo org untouched. Tracked item:
  `docs/FIX_BEFORE_PROD.md` → "Resolve the cross-tenant email-existence oracle (AUD-04)".

## Steps
1. Onboard A and B.
2. Admin A `POST /users` with a guaranteed-fresh email → expect 201 (control).
3. Admin A `POST /users` with **B admin's email** → expect 409 (the oracle hit).
4. Admin A `POST /users` with a never-registered random email → expect 201 (discriminator).

## Expected result
Per contract this *should* not distinguish cross-tenant existence; the documented reality
is it **does**: 201 (fresh) vs 409 (cross-tenant) vs 201 (random). Recorded as a Fail
(the leak reproduces) tagged CONFIRMS-DOCUMENTED.

## Harness
Script: `harness/tc_034.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_034.py`

---

## Execution result

- **Run at:** 2026-05-31 11:41 local
- **Result:** ❌ Fail (documented oracle reproduced — the intended finding)
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> Admin A's POST with B's admin email returned `409 "A user with this email already
> exists."`; a fresh email and a never-registered random email both returned `201`. The
> 409-vs-201 split lets A learn that `tenant-b-admin-…@tenant.oneai` belongs to a user in
> another tenant — a cross-tenant existence disclosure.

**Evidence**

```
ONBOARD A: 201 | ONBOARD B: 201
B admin email (exists in OTHER tenant): tenant-b-admin-19e7d31c80e0912@tenant.oneai
PROBE fresh email: tenant-fresh-19e7d31c80e0912@tenant.oneai
  -> status: 201 body: {"id":"0e23d155-1876-497c-bd20-67aff566004c","email":"tenant-fresh-19e7d31c80e0912@tenant.oneai",...,"org_id":"7a27eb64-e603-41df-8177-6c48b7d660fa",...}
PROBE cross-tenant email: tenant-b-admin-19e7d31c80e0912@tenant.oneai
  -> status: 409 body: {"detail":"A user with this email already exists."}
PROBE never-registered email: tenant-never-19e7d31c80e0912-27600d@tenant.oneai
  -> status: 201 body: {"id":"6f585400-2fb9-467b-a548-9510ee2874b4",...,"org_id":"7a27eb64-e603-41df-8177-6c48b7d660fa",...}
ORACLE DEMONSTRATED: True (409 for a cross-tenant address vs 201 for fresh == existence leak)
```

**Verdict**

Defense **broke as documented** — Low severity, cross-tenant *existence* disclosure only
(no content). Code path: `UserService.create_user` calls
`UserRepository.email_exists(email)` which runs `select(User.id).where(User.email == email)`
with **no org filter** (`user_repository.py:76-79`); a hit raises `DuplicateUserError` → 409
(`user_service.py:63-64`). This **confirms AUD-04** as a still-open, tracked deferral
(`docs/FIX_BEFORE_PROD.md`, "Resolve the cross-tenant email-existence oracle (AUD-04)") —
global email uniqueness is intentionally retained for now. Not a new finding.

**Notes / follow-up**

Self-limiting nuance from the audit holds: a *negative* probe (201) creates a real user in
A's own org, so re-probing a burned address then 409s from A's own row, polluting the
signal and leaving an audit trail of created-then-deactivated junk. The oracle is clean on
the *first* probe of any address. Remediation options (per the tracked item): per-org
uniqueness `UNIQUE(org_id, email)` + `email_exists_in_org`, or rate-limit + accept.
