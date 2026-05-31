# One AI — Validation & Adversarial Testing

> **Purpose.** This is the **dynamic, adversarial test suite** for One AI. Where the
> documents under `docs/audits/` are *static* code reviews, this suite **runs the real
> stack and tries to break it** — proving (or refuting) that the system behaves
> correctly under hostile input, concurrency, and cross-tenant attack. The goal is not
> "does the happy path work" but **"what input, sequence, or boundary makes this fall
> over or leak."**

---

## 1. Philosophy

1. **The goal is to break the code.** Every test case carries a *break hypothesis* — a
   concrete prediction of how and where the code fails — and then tries to make it fail
   for real against a running system. A test that cannot fail proves nothing.
2. **Adversary mindset.** We think like an attacker and like a careless operator: forged
   tokens, cross-tenant probes, race conditions, malformed payloads, boundary values,
   tokens that outlive their account.
3. **Empirical, not theoretical.** Findings are backed by real request/response evidence
   against the live uvicorn + Postgres, never by code-reading alone. This is the
   complement to the static audits, whose own Limitations section flags exactly this gap
   ("no dynamic testing, no concurrency load test to observe the races firing").
4. **Tenant isolation is the hardest rule.** A cross-company data leak is a contract and
   GDPR breach. Every tenant-scoped surface gets a cross-tenant negative test
   (`.claude/rules/testing.md` non-negotiable).

## 2. Relationship to the existing audits

This suite does **not** rehash documented deferrals. Two prior documents are the baseline:

- `docs/audits/2026-05-30_identity-module-deep-audit.md` — 15 findings (13 marked fixed).
- `docs/FIX_BEFORE_PROD.md` — the forward checklist of accepted, tracked trade-offs.

Every result here is tagged against that baseline (see **Finding tags** below). The value
of this suite is the **NEW** column plus the **empirical verdicts** (FIXED / REFUTED) on
the audit's claims — not re-describing gaps that are already written down. Known,
documented deferrals are proven **once** to characterize them, tagged `CONFIRMS-DOCUMENTED`,
and not dwelt on.

## 3. Folder structure

```
testing/
  README.md                       ← this file (strategy)
  TEMPLATE.md                     ← copy this to author a new test case
  <NN>_<target>/                  ← one folder per testing TARGET
    README.md                     ← target scope, environment, setup, status dashboard
    harness/                      ← executable scripts that drive the live stack
    TC-<TT>-<NNN>_<slug>.md       ← one file per TEST CASE (author → run → write result back)
```

A **target** is a coherent slice of the system under test (e.g. *Infrastructure +
AuthN/AuthZ*). Targets are numbered in the order we test them. The current and planned
targets are listed in the roadmap (§8).

## 4. Test-case lifecycle

Each test case is a living document that moves through three states **in place**:

1. **Author** — copy `TEMPLATE.md`, fill Objective / break hypothesis / Preconditions /
   Steps / Expected. Status `Draft`. Result `⬜ Not run`.
2. **Execute** — run the harness against the live stack.
3. **Record** — write the **Execution result** block back into the *same file*: outcome,
   actual behavior, raw evidence, verdict, finding tag. Status `Executed`.

The case file is the durable artifact. The target `README.md` is the dashboard that
indexes every case with its current status and result.

## 5. Conventions

### ID scheme
`TC-<TARGET>-<NNN>` — e.g. `TC-IA-007`. Target codes:

| Code | Target |
|---|---|
| `IA` | Infrastructure + Authentication/Authorization |
| `UM` | User management (CRUD, last-admin, lifecycle) — *planned* |
| `IV` | Input validation & fuzzing — *planned* |
| `FE` | Frontend (auth client, routing, XSS) — *planned* |

### Result legend
In adversarial testing, **a ❌ Fail is a win** — we broke something. Read results as
*behaviour vs. the contract*, not as "the test script errored."

| Result | Meaning |
|---|---|
| ⬜ Not run | Authored, not yet executed |
| ✅ Pass | System behaved per the contract — the defense held / behaviour correct |
| ❌ Fail | Contract violated / defect reproduced — **a finding** |
| ⚠️ Pass-with-concern | Behaved acceptably but with a caveat worth recording |

### Finding tags
| Tag | Meaning |
|---|---|
| 🆕 `NEW` | A defect or risk **not** in the prior audits or `FIX_BEFORE_PROD.md` |
| ✔ `CONFIRMS-FIXED` | Empirically proves a prior audit fix holds under real conditions |
| ✖ `REFUTES-FIX` | A claimed fix does **not** hold in the running system |
| 📋 `CONFIRMS-DOCUMENTED` | Reproduces an already-tracked/deferred item (characterize once) |
| — | Not applicable (pure positive/contract test) |

### Severity (when a case Fails)
`Critical` · `High` · `Medium` · `Low` · `Info` — aligned with the existing audit and
`.claude/rules/security.md`. Cross-tenant data exposure is never below High.

### Test type
`Positive` · `Negative` · `Boundary` · `Adversarial` · `Concurrency` · `Fuzz`.

## 6. Tooling & how to run

- **Backend driver.** Harness scripts are Python (`httpx` + `asyncio` for true
  concurrency, `pyjwt` to forge/inspect tokens). They run **inside the backend
  container** against the real server, because the races we hunt live in the
  transaction/connection-pool boundaries — the in-process ASGI test client would hide
  them. The `testing/` tree is not volume-mounted into the container, so scripts are
  piped over stdin:

  ```bash
  docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/<script>.py
  ```

  Harness scripts are **self-contained** (they inline a small COMMON block) so they run
  unchanged over stdin and stand alone as reproducible evidence.
- **Frontend driver.** Playwright (MCP browser tools) against `http://localhost:5173`.
- **Concurrency.** Real parallel HTTP via `asyncio.gather`; races are run for **many
  iterations** — a single non-firing trial never earns a "safe" verdict.

## 7. Environment

| Item | Value |
|---|---|
| Stack | `docker compose up` — `db` (pgvector/pg16), `backend` (:8000), `frontend` (:5173) |
| API base | `http://localhost:8000` |
| DB | **persistent** shared volume (`db-data`) — no per-test truncation |
| RLS | **defined but inert** (app connects as superuser `oneai`); active control is the app-layer `org_id` filter |
| JWT secret | dev default `dev-only-insecure-secret-change-me-in-prod` (forgeable — see TC-IA authZ cases) |

Because the DB is persistent and shared, harness setup creates **fresh, run-stamped
orgs and unique emails per run** and never mutates the demo org's admin (stranding it
would break the login page's dev panel with no in-app recovery).

Demo credentials (dev-only, from `docs/FIX_BEFORE_PROD.md`):

| Scope | Email | Password |
|---|---|---|
| Platform admin | `super@ethera.ai` | `Sup3r-Dev-Only-2026!` |
| Company admin | `admin@demo.oneai` | `Adm1n-Dev-Only-2026!` |
| Member | `member@demo.oneai` | `Memb3r-Dev-Only-2026!` |

## 8. Targets roadmap

| # | Target | Status |
|---|---|---|
| 01 | Infrastructure + AuthN/AuthZ | **in progress** |
| 02 | User management (CRUD, last-admin, lifecycle) | planned |
| 03 | Input validation & fuzzing | planned |
| 04 | Frontend (auth client, routing, XSS) | planned |

> Connectors (Connect), retrieval (Ask), and the learning loop (Learn) get their own
> targets as those modules land.
