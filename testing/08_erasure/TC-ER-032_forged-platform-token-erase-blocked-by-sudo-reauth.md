<!--
  Test-case template. Copy this file to testing/<NN>_<target>/TC-<TT>-<NNN>_<slug>.md
  and fill every section. Author the top half BEFORE running; write the
  "Execution result" block back into this same file AFTER running.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-ER-032: A forged platform token (dev secret, random sub) CANNOT erase an org — blocked by an undocumented sudo password re-auth (403)

| Field | Value |
|---|---|
| **ID** | TC-ER-032 |
| **Target** | GDPR erasure + compliance export (PC-06) |
| **Suite** | AUTHZ — audience confinement + forged-token blast radius |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | NEW |

## Objective
Characterize the *destroy* blast radius of a forged platform token (dev secret + random `sub`) against the
IRREVERSIBLE erase endpoint. The task premise (and the PC-06 docs) say a forged platform token erases any
org. Test it against the LIVE stack and record what actually happens.

## Break hypothesis
Original bet (per task / PC-06 docs): the platform gate verifies only signature + audience + expiry, so a
dev-secret forgery with a random `sub` drives a full erase → **200**, destroying any org by anyone holding
the dev secret. A positive control (real super admin + correct password) isolates whether the erase path
itself works end-to-end.

## Preconditions
- Live stack `:8000`. Suite code **AUTHZ**, run-stamped slug (lowercase `[a-z0-9-]`).
- **⚠️ IRREVERSIBLE — this case may DESTROY data.** Onboard ONE brand-new throwaway AUTHZ org Z via
  `provision_company` *this run*; touch ONLY Z. Never a literal id, never demo/globex/another suite's org.
- `forge_platform_token()` (random sub, dev secret, valid exp).
- NOTE: the shared `erase_org()` helper sends only `{reason, confirm_slug}`; the LIVE `ErasureRequest`
  schema additionally requires `password`, so this case POSTs the erase body directly (helper omits it).

## Steps
1. `provision_company("authz-forge-…")` → fresh org Z `{org_id, slug}`.
2. **Part A (forged):** `forge_platform_token()` (random sub) → POST `.../erase` with body
   `{reason, confirm_slug=Z.slug, password="<guessed>"}`.
3. **Part B (positive control):** real super-admin token + body `{…, password=PLATFORM_PW}` → POST
   `.../erase` for the same Z.
4. psql ground-truth: Z's user count, status, `org.erased` row + its actor_id.

## Expected result
- The forged attempt → erase succeeds (200) IF the gate is signature/audience/expiry only; OR is blocked
  if an additional credential check exists. Either way, record the ACTUAL behavior.
- The positive control → 200 (proves the erase path works for a legitimately-authenticated admin).
- psql: reflects only the erases that actually committed.

## Harness
Script: `harness/tc_032.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_032.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 18:43 local (live stack)
- **Result:** ✅ Pass
- **Finding tag:** NEW

**Actual behavior**

> The documented "forged token erases any org" capability **did NOT reproduce.** A platform-aud token
> forged from the dev secret with a random `sub` was **rejected with 403 "Password confirmation failed."** —
> the erase was blocked, nothing was destroyed. The live `ErasureService.erase_organization` now performs a
> **sudo-style password re-auth**: it loads the acting admin by `actor.subject_id` and verifies the
> body-supplied `password` against that admin's hash BEFORE any delete. A forged random `sub` resolves to no
> admin row → 403. The positive control (real super admin + correct password) erased Z cleanly → 200,
> proving the erase path itself works; psql attributes the single `org.erased` row to the REAL admin, never
> a forged sub. This sudo password gate is **absent from the source-of-record PC-06 docs** (the PR-6 review,
> the EPIC, the target README all describe only slug→legal-hold; none mention `password`/`403`/re-auth).

**Evidence**

```
# harness stdout
Z_SLUG authz-forge-19e847fcadff419
Z_ORG_ID caf09d2f-eb02-4e33-b49c-d8d731bb190d
FORGED_ERASE_STATUS 403
FORGED_ERASE_BODY {"detail":"Password confirmation failed."}
REAL_ERASE_STATUS 200
REAL_ERASE_BODY {"org_id":"caf09d2f-...","org_slug":"authz-forge-19e847fcadff419","status":"offboarded",
  "erased_by_admin_id":"2b940f53-428a-4a76-8a7a-2c27b4983963","users_erased":1,"tokens_deleted":1,
  "support_decider_emails_scrubbed":0,"audit_log_retained":true, ...}

# raw 422 from an earlier run proving the schema now requires `password`
# (forged_erase via the shared helper, which omits password):
#   422 {"type":"missing","loc":["body","password"],"msg":"Field required"}

# live OpenAPI ErasureRequest.required == ["reason","confirm_slug","password"]

# psql ground-truth (db container)
           slug             |   status   | users | erased_rows |              erased_by
----------------------------+------------+-------+-------------+--------------------------------------
 authz-forge-19e847fcadff419 | offboarded |     0 |           1 | 2b940f53-428a-4a76-8a7a-2c27b4983963
```

**Verdict**

The defense **held — stronger than the documented threat model.** The forged-token erase was blocked at a
sudo-style password re-auth in `ErasureService.erase_organization`
(`backend/app/identity/services/erasure_service.py`: loads `platform_admins.get_by_id(actor.subject_id)`,
then `verify_password(payload.password, admin.password_hash)` → `PasswordConfirmationError` (403) before
the legal-hold check and before any delete). Order is now: lock → slug (400) → **password (403)** →
legal-hold (409) → deletes. The live `ErasureRequest` schema requires `password` (committed in
`13da7fe feat(platform): require password re-authentication to erase a company (sudo-style)`).

**This is tagged NEW** — not because of a vulnerability, but because the running stack contains a
**security control that is undocumented in every PC-06 source-of-record** the suite was pointed at (PR-6
review `docs/audits/2026-06-01_platform-erasure-pr6-review.md`, `EPIC-PC-06-erasure.md`, and the target
README — all describe only slug + legal-hold, none mention password re-auth or a 403 path). Consequently
the documented "forged platform token erases any org" deferral is **REFUTED for the erase endpoint as it
runs today**: a leaked dev secret alone is **insufficient** to destroy a tenant — an attacker would ALSO
need a real platform admin's password (credential compromise, a strictly higher bar than secret-leak).
The blast radius is materially reduced versus the documented model.

**Notes / follow-up**

- This DIRECTLY conflicts with the task's instruction that TC-ER-032 is "CONFIRMS-DOCUMENTED, never NEW,
  expect 200." That instruction assumed the pre-`13da7fe` behavior; the live stack does not exhibit it.
  Honest reporting requires NEW + PASS here. (The DESTROY capability the task feared does not exist today.)
- Asymmetry with TC-ER-033: the **export** endpoint has NO password gate, so a forged token still reads any
  org's trail (200). Net: a leaked dev secret can READ any org's audit trail but CANNOT DESTROY a tenant.
- The "Rotate JWT_SECRET" deferral (`docs/FIX_BEFORE_PROD.md`) still stands as the root for the *read* side
  (TC-ER-033) and for cross-domain forgery generally; the password gate narrows only the *destroy* side.
- Recommend the PC-06 docs (review/EPIC/README) be updated to record the sudo re-auth as an AC, since the
  running behavior currently outpaces the documentation.
