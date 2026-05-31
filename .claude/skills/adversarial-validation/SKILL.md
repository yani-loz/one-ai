---
name: adversarial-validation
description: Dynamic, adversarial validation of a module/feature against the LIVE stack — break the code, prove tenant isolation, fire concurrency races, fuzz inputs, and empirically verify (or refute) a prior static audit's fix claims. Produces a structured testing/<NN>_<target>/ suite (one file per test case, author→execute→record) plus a consolidated audit doc under docs/audits/. Use when asked to validate / stress-test / "break" / QA / dynamically test a backend module, endpoint set, or feature.
disable-model-invocation: true
---

Adversarially validate (break) the target: **$ARGUMENTS**

You are an adversarial QA validator. The goal is **not** "does the happy path work" — it is
**"what input, sequence, or boundary makes this fall over or leak."** Every test case carries a
*break hypothesis* and tries to make it fail for real against the **running stack**. A test that
cannot fail proves nothing. This is the dynamic complement to the static audits in `docs/audits/`.

> Reference implementation (the pass this skill is distilled from): `testing/01_infrastructure-authn-authz/`
> + `docs/audits/2026-05-31_identity-dynamic-adversarial.md`. Bundled starters: `assets/TEMPLATE.md`,
> `assets/harness_common.py`, `assets/workflow.example.js`.

## Principles (do not skip)

1. **Goal is to break it.** Think like an attacker *and* a careless operator: forged tokens,
   cross-tenant probes, race conditions, malformed/boundary payloads, tokens that outlive accounts.
2. **Empirical, not theoretical.** Every finding is backed by real request/response evidence against
   the live server — never code-reading alone.
3. **Tenant isolation is the hardest rule** (`.claude/rules/security.md`). Every tenant-scoped surface
   gets a cross-tenant negative test. A cross-company leak is a contract/GDPR breach.
4. **Don't rehash documented deferrals.** Read `docs/audits/*` and `docs/FIX_BEFORE_PROD.md` first.
   Tag every result `NEW` / `CONFIRMS-FIXED` / `REFUTES-FIX` / `CONFIRMS-DOCUMENTED`. The value of the
   pass is the **NEW** column + live **verdicts** on prior claims — prove a known deferral *once* and
   move on.
5. **Concurrency needs rigor.** Run races **many iterations** (≥50) — one non-firing trial never earns
   a "safe" verdict. Same-row races serialize on the DB row lock *by design*, so a black-box pass
   there is *corroboration confirmed by code review*, not independent proof (contrast different-row
   races, which leave positive artifacts like UNIQUE-violation DB-log lines). Use a positive control
   (a known-firing race on the same pool) to prove contention actually engaged.
6. **Re-verify your headline NEW findings yourself**, first-hand, independent of any sub-agent.

## Procedure

### 0 — Orient
- Read the target's code surface: routes → services → repositories → models, schemas (the validation
  boundary), dependencies/auth gates, migrations, error handlers, middleware.
- Read the prior audits (`docs/audits/`) and `docs/FIX_BEFORE_PROD.md` so you tag correctly and don't
  re-discover tracked items.
- Confirm the stack is up: `docker compose ps`; `curl -s http://localhost:8000/health`. If down, offer
  `docker compose up --build`.
- Note the demo credentials (`docs/FIX_BEFORE_PROD.md` / the seed script) and key facts: tenant key is
  **`org_id`**; **RLS is defined-but-inert** (app connects as Postgres superuser → the app-layer
  `org_id` filter is the *only* active isolation control); the dev `JWT_SECRET` is a known forgeable
  default. **Call advisor** before committing to the test plan.

### 1 — Scaffold the suite
Create `testing/<NN>_<target>/` (next number in `testing/`; one folder per target). If the root
`testing/README.md` / `testing/TEMPLATE.md` don't exist yet, create them (strategy + the bundled
`assets/TEMPLATE.md`). Then:
- `testing/<NN>_<target>/README.md` — scope, environment, and a **status dashboard** table (you own
  this file; fill the Result/Tag columns during synthesis).
- `testing/<NN>_<target>/harness/_common.py` — copy `assets/harness_common.py` and adapt the CONFIG
  block (base URL, demo creds, the onboarding/login helpers) to this target.

### 2 — Validate the harness end-to-end (de-risk before any fan-out)
Run one script that exercises the whole helper surface (login, provision data, forge a token, one
real probe) against the live server. **Do not fan out until the plumbing is proven.** Harness scripts
run **inside the backend container against the real uvicorn** (transaction/pool behaviour — where
races live — is invisible to the in-process ASGI client):

```
docker compose exec -T backend python - < testing/<NN>_<target>/harness/<script>.py
```

The `testing/` tree is **not** volume-mounted into the container, so each harness script is
**self-contained**: paste the full contents of `_common.py` at the top, append your
`async def main(): ...` + `asyncio.run(main())`, and pipe it over stdin (an `import _common` fails).

### 3 — Enumerate test cases
From the code **and** your QA experience, list cases across: Positive, Negative, Boundary,
Adversarial, Concurrency, Fuzz. Cover at minimum (adapt to the target):
- **Infra:** health, body-size cap (+ chunked/no-Content-Length bypass), CORS, RLS posture, error-path
  hygiene (404/405/422 leak no stack/secret).
- **AuthN:** valid login, wrong-password vs unknown-account (byte-identical → no enumeration), inactive
  account, password byte-limit (no 500), platform vs company.
- **AuthZ:** role gate (→403), audience split both directions (→401), missing bearer (→401 not 403),
  `alg=none`, tampered signature, expired, missing `exp/aud/sub`, malformed claims (→401 not 500).
- **Cross-tenant:** A can't PATCH/DELETE B (→404), list returns own-org-only, `org_id` not smuggle-able
  via body, the email-existence oracle, **forged-token cross-tenant read+write** (proves isolation has
  no second layer while RLS is inert), forged role escalation.
- **Token lifecycle:** rotation single-use (+ the concurrent race), logout idempotency, refresh after
  logout, deactivated account's tokens, demoted-admin-keeps-power (no denylist).
- **Concurrency (high value):** any check-then-act guard (last-admin, dup-create, onboarding rollback) —
  fire concurrent requests, assert the invariant holds, ≥50 iterations, read post-state via psql or a
  forged token.
- **Input/fuzz:** byte vs char limits, role-enum escalation via body, SQL/script injection stored
  literally, **email canonicalization** (case-variant duplicates + case-fragile login), control/NUL
  chars (→422 not 500), field bounds.

Pre-tag each case with the verdict you *expect* from code review; the executed result confirms or
overturns it.

### 4 — Execute
Author each case from `TEMPLATE.md` (Objective, break hypothesis, preconditions, steps, expected),
run its harness, then write the **Execution result** block back into the *same file* (raw evidence —
status codes + bodies, not paraphrase — plus verdict + finding tag + the `file:line` code path).
- **One file per test case:** `TC-<TT>-<NNN>_<slug>.md`.
- For breadth, fan out with a **multi-agent workflow** (see `assets/workflow.example.js`): one
  suite-agent per sub-area, **each in its own run-stamped data namespace** so they don't collide on
  the shared DB. Keep timing-sensitive concurrency races in a dedicated agent (or do them yourself).

### 5 — Verify headline NEW findings yourself
Re-run each NEW defect with your own harness, many iterations, and confirm via psql ground-truth.
Apply the same-row/different-row caveat (Principle 5) before calling any race "safe".

### 6 — Synthesize
- Fill the target `README.md` status dashboard (Result + Tag per case, links to each file).
- Write a consolidated `docs/audits/<YYYY-MM-DD>_<target>-dynamic-adversarial.md`: exec summary, NEW
  findings (severity, evidence, `file:line`, remediation), documented deferrals reproduced, empirical
  verdicts on prior fixes, what held, coverage & limitations. **Call advisor** before declaring done.

### 7 — Hand back
Surface the decisions that are the user's: append NEW findings to `FIX_BEFORE_PROD.md`? clean up the
test-polluted dev DB (`TRUNCATE` identity tables + re-seed)? what target next? Offer, don't assume.

## Hard rules & gotchas (learned the hard way)

- **Shared persistent DB:** never rely on truncation between cases. Provision **fresh run-stamped orgs
  and unique emails per run** (`stamp()` helper). Never mutate the demo org/admin (stranding it breaks
  the login page's dev panel with no in-app recovery).
- **Never write under `backend/` or `frontend/` during a live run** — it triggers uvicorn `--reload`
  and drops in-flight connections, corrupting races. All harness/case files live under `testing/`.
- **psql ground-truth** runs against the **db** container, not backend (the backend image has no psql):
  `docker compose exec -T db psql -U oneai -d oneai -c "<SQL>"`.
- **RLS is inert today** — verify it (bogus `app.current_org_id` GUC still returns rows as superuser)
  but the *active* control is the app-layer filter; forged-token cases prove the JWT secret is the
  single isolation layer.
- **Reserved email TLDs** (`.test`, `.example`) are rejected by `email-validator` — use a real-looking
  TLD (`@t.io`, `@oneai.dev`) for test accounts.
- **Concurrent actors:** the dev stack may be in use by another session. If post-truncate counts
  re-populate with stamps later than yours, someone else is running — don't `TRUNCATE` into their
  writes; report and ask.
- **Result semantics:** ✅ Pass = the defense held (behaviour correct); ❌ Fail = contract violated = a
  defect = **the win we're hunting**. A harness bug is not a Fail — fix the harness and rerun.

## Conventions (mirror `testing/README.md`)

- **ID:** `TC-<TARGET>-<NNN>` (e.g. `IA`, `UM`, `IV`, `FE`).
- **Result:** ⬜ not run · ✅ pass · ❌ fail (a finding) · ⚠️ pass-with-concern.
- **Finding tag:** 🆕 NEW · ✔ CONFIRMS-FIXED · ✖ REFUTES-FIX · 📋 CONFIRMS-DOCUMENTED · — n/a.
- **Severity:** Critical / High / Medium / Low / Info. Cross-tenant data exposure is never below High.
