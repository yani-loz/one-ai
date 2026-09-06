# GDPR erasure + compliance export (PC-06) — Dynamic Adversarial Validation

> **Status as of 2026-09-06 (dated record — the findings below are unchanged):** the documentation gap is closed, the scaffold-loss note is no longer true, and the security finding is **still live**.
> **§3's doc-lag and §4 recs 1–2 are closed.** The sudo password re-auth is now in the sources of record: `docs/PM/platform-console/EPIC-PC-06-erasure.md:32` and its AC row `:67`, the PR-6 review (`docs/audits/2026-06-01_platform-erasure-pr6-review.md:37`), and `docs/FIX_BEFORE_PROD.md:46`, which now carries the refined blast-radius wording (*"company erasure now also requires a sudo-style password re-auth (commit `13da7fe`), so a forged token alone returns 403 there (TC-ER-032) — erase is the only forged-token write that's second-factored; every other write + all reads are not"*).
> **The scaffold loss did not survive.** The Scope block, §5 "Scaffold loss" and §6 "Scaffolds removed" say the `testing/07`/`08` per-case files were deleted; commit `b5bee33` *"revert(testing): restore the dynamic-adversarial QA passes I wrongly removed in 3966800"* (2026-06-02) restored them, and `git ls-files testing/08_erasure` returns 31 tracked files today (`testing/07_break-glass`, 22).
> **Still LIVE:** the read/destroy asymmetry this pass identified (§1 "The real posture", §4 rec 3). `backend/app/identity/services/erasure_service.py:110-130` shows the sudo re-auth guarding `erase_organization` **alone**; no other platform write or read is second-factored, and the `Rotate JWT_SECRET` item at `docs/FIX_BEFORE_PROD.md:46` is still unchecked. §5's "PC-06b frontend is a separate Playwright pass" also remains open — no such pass exists in `docs/audits/`.

> **Scope:** the PC-06 erasure backend (`feat/platform-erasure`) — `POST /platform/orgs/{id}/erase`
> (slug-confirmed, legal-hold-gated, atomic) + `GET …/compliance-export`. Dynamic complement to
> `2026-06-01_platform-erasure-pr6-review.md` and `EPIC-PC-06-erasure.md`.
>
> **Method:** a 4-suite `Workflow` (HOLD / ERASE / RETAIN / AUTHZ) ran **16 cases** against the live stack
> (real uvicorn `:8000`, psql + audit-trail ground-truth). 4 agents, ~564k tokens. **The headline result was
> lead-verified first-hand** (`harness/_reverify_authz.py`). ⚠️ Run conditions were turbulent (see §6): the
> erase **code changed mid-validation** (a new sudo-reauth commit landed), the dev DB was wiped/reseeded, and
> the `testing/07`/`08` scaffolds were removed by the repo owner — so the durable record of these findings is
> **this audit doc**, backed by the workflow output + the lead re-verification.

## 1. Executive summary

**16 cases · 15 ✅ · 1 ⚠️ · 0 ❌ · 1 🆕.** PC-06 erasure is **sound** on every contract (legal-hold,
slug-confirm, completeness, atomicity, PII scrub, audit retention, export, audience). But the **headline
flipped vs. the documented blast radius**, and a new control surfaced:

- **🆕 The forged-token erase is now BLOCKED (TC-ER-032).** Commit `13da7fe` *"require password
  re-authentication to erase a company (sudo-style)"* added a **sudo password re-auth** to the erase path:
  `lock → slug 400 → password 403 → legal-hold 409 → deletes` (`erasure_service.py:107-109`:
  `get_by_id(actor.subject_id)` then `verify_password`). A forged dev-secret token with a random `sub`
  resolves to **no admin → 403**, so it **cannot erase** a tenant. This **refutes the documented
  "forged token erases any org" deferral for the erase endpoint as it runs today** — a leaked dev secret
  alone is now insufficient *to erase*; an attacker also needs a real admin's password (credential
  compromise, a higher bar). The control is real and in git (HEAD = container), but **undocumented** in the
  PC-06 review, epic, README, and `FIX_BEFORE_PROD` (doc lag). Tagged 🆕 (a documented premise empirically
  refuted + an undocumented control), not CONFIRMS_DOCUMENTED.
- **⚠️ Every *other* forged-token capability is unchanged (TC-ER-033, CONFIRMS_DOCUMENTED).** Commit
  `13da7fe` touched only `erasure_schemas`/`erasure_service` — **not** `platform_org_service` or the support
  services. So a forged dev-secret token still: reads any org's compliance export (TC-ER-033 → **200**,
  metadata + trail), reads the fleet (`/platform/orgs`), **suspends / legal-holds any org** (TC-OL-024 →
  200, a one-request availability lockout of any customer), and **self-approves break-glass support access**
  (TC-BG-003 → 200). The disclosed `Rotate JWT_SECRET` deferral stands for all of these.

**The real posture (corrected — do not over-read this as "destroy is closed"):** commit `13da7fe` sudo-gated
**erase ALONE.** The asymmetry is therefore narrow: **erase now needs a second factor; every other forged
write — suspend, legal-hold, support-approve — and all reads do not.** A forged dev-secret token can still
**lock every customer out** (suspend is destructive to availability) and read everything; it just can't run
the irreversible data-deletion. "A leaked dev secret can't destroy a tenant" would be **false** — only the
*erase* path gained the extra gate.

### Lead re-verification (first-hand, `_reverify_authz.py`)
```
A) FORGED random-sub erase:        403 'Password confirmation failed.'   (BLOCKED)
B) REAL admin + correct password:  200  users_erased=1                  (positive control)
C) REAL admin + WRONG password:    403 'Password confirmation failed.'  org still 'active' (nothing touched)
D) erase WITHOUT password field:   422  (field required)
E) FORGED token compliance export: 200  keys=[organization,audit,generated_at]  (read still works)
```

## 2. What held (live, psql-corroborated)

| Suite | Cases | Result |
|---|---|---|
| HOLD | TC-ER-001..004 | ✅ legal-hold → 409 nothing-touched (users/tokens/decider-email/status/audit all intact, psql); slug-mismatch → 400; **order slug-before-legal-hold** confirmed (400 wins over 409); row-lock TOCTOU corroborated (32 iters, self-consistent end states, no 5xx) |
| ERASE | TC-ER-010..013 | ✅ honest certificate (users_erased/tokens_deleted/audit_retained); **PII sweep**: 0 surviving users, 0 orphan tokens, `decided_by_email` NULL (scrubbed) but `requested_by_email` kept (Ethera staff), org retained `offboarded`; erased admin login → 401; re-erase idempotent (200, zero counts) |
| RETAIN | TC-ER-020..023 | ✅ append-only audit RETAINED through erase + `org.erased` appended (Art. 17(3)); export = 7-field metadata + trail, content-blind (no secret-value hits); export-after-erase builds (regulator proof); unknown org → 404 both endpoints; **wrong-password erase → 403, untouched** (corroborates the sudo-reauth negative path) |
| AUTHZ | TC-ER-030..033 | ✅ company-aud token (real admin sub) → 401 (discriminating); real company_admin token → 401 (no self-erase/export); **🆕 TC-032 forged erase BLOCKED (403)**; ⚠️ TC-033 forged export 200 |

The retained `audit_log.actor_email`/`ip_address` is the **documented Art. 17(3) table-scope retention** the
certificate discloses — correctly **not** flagged as a leftover (the PII sweep targets undocumented
survivors only: a surviving user, an unrevoked token, an un-nulled `decided_by_email` — none found).

## 3. The new sudo-reauth control (positive hardening, doc-lagged)

`commit 13da7fe` added: a `password: LoginPassword` field on `ErasureRequest` (`erasure_schemas.py:37` — the
bounded login-password type my earlier N-04 finding prompted), a `PasswordConfirmationError → 403`, and the
re-auth in `erase_organization` **after** slug and **before** legal-hold. Negative path verified live: a
real admin with a wrong password → **403, nothing touched** (TC-ER-023 + lead re-verify). It is a genuine
defense-in-depth on the single most destructive endpoint. **The gap is documentation:** no PC-06
source-of-record mentions it, and `FIX_BEFORE_PROD`'s `Rotate JWT_SECRET` note still implies a forged token
can erase — which is now false for erase (true for export).

## 4. Recommendations (proposed — durable record only; not applied)

1. **Document the sudo-reauth** in `EPIC-PC-06`, the PR-6 review, and the erase README/contract (the
   `password` field, the 403 path, the order slug→password→hold).
2. **Refine the `Rotate JWT_SECRET` blast-radius note** in `FIX_BEFORE_PROD`: a forged dev-secret token can
   still **read** any org (compliance-export, `/platform/orgs`, support metadata) but can **no longer erase**
   (sudo-reauth blocks it). The remaining destroy paths (suspend/legal-hold via PATCH, support self-approve)
   are *not* sudo-gated — consider whether the read paths and other writes warrant the same second factor.
3. **Note the read/destroy asymmetry** as the current security posture for the control plane.

## 5. Coverage & limitations

- **Backend only.** PC-06b's "Erase / Export" **frontend** (the confirmation modal + the password prompt,
  `OrgErasurePanel.tsx`) is a separate Playwright pass.
- **Same-row race = corroboration** (TC-ER-004): the erase-vs-legal-hold orderings share an end state, so
  this corroborates the `FOR UPDATE` lock (confirmed by code review), not independent proof — as the PR-6
  review itself notes.
- **Scaffold loss:** the per-case `testing/08_erasure/TC-ER-*.md` files for the ERASE suite (010–013) were
  lost to mid-run filesystem churn + the owner's scaffold-removal commit; their evidence is preserved in the
  workflow output and summarized above. `testing/07` (break-glass) was removed entirely (its audit doc
  survives separately).

## 6. Run conditions (process record)

- **Code changed mid-validation:** `13da7fe` (sudo-reauth) + `81a8655`/`500d539` (PC-06b UI) landed during
  the run, which is why an early lead probe erased *without* a password and the agents' later runs required
  it. `git diff HEAD` is clean (no deployed-vs-source divergence — an earlier agent's "binary mismatch"
  claim was a transient filesystem-view glitch; the password field IS at HEAD). The validation is against
  the **current** code; all `file:line` cite HEAD.
- **DB reseeded mid-run** by a concurrent actor (demo `platform_admins` row id changed); the suites recovered
  via legitimate login. **Demo/globex orgs were never erased** (only run-stamped orgs).
- **Scaffolds removed:** the owner committed `3966800` removing `testing/07`/`08`; this audit doc is the
  durable home for the findings.
