# Dynamic-adversarial validation — JWT fail-closed secrets gate + RLS standing-invariant test

> **Status as of 2026-09-06 (dated record — the findings below are unchanged):**
> **§7 follow-up 2 is done (TC-SG-015 closed).** `_SENTINEL_TABLE` no longer exists anywhere under `backend/` (0 grep hits); `backend/tests/identity/models/test_rls_invariants.py:98-106` now keys the skip on the migration-independent `alembic_version` table (`_is_migrated`), so a forgotten `org_isolation` policy on `users` FAILs instead of SKIPping.
> **The §1 scope sentence, TC-SG-011 (§3 Suite B) and the §5 boundary (Suite C / TC-SG-020) are superseded.** *"DB-level RLS remains inert by design (the app connects as superuser/owner `oneai`) … cross-org reads remain open at runtime until migration `0007`"* describes the pre-enforcement world; the flip shipped as migration **`0009_enforce_rls.py`** (the `oneai_app` / `oneai_global` role split + `FORCE ROW LEVEL SECURITY`, with `0019` adding the SELECT-only `oneai_reader`). The same invariant test now asserts FORCE at `:170-171`, and on the live dev DB 22 tables carry ENABLE + FORCE + an `org_isolation` policy, measured 2026-09-06 (`docs/audits/2026-09-06_built-vs-docs-map.md` §3).
> §7 follow-up 1 was already closed in-document; follow-up 3 (`.strip()` on `app_env`) is not tracked here.

| | |
|---|---|
| **Date** | 2026-06-02 |
| **Target** | 09 · code `SG` (`testing/09_secrets-gate-rls-invariant/`) |
| **Validates** | `f8a4fbd` (fail-closed secrets gate, `backend/app/core/config.py`) + `3415ce1` / `f8a4fbd` (RLS standing-invariant test, `backend/tests/identity/models/test_rls_invariants.py`) |
| **Method** | Multi-agent workflow — 3 parallel adversarial suites (live stack) → independent verify pass → synthesis. 18 cases. |
| **Stack** | `docker compose` live: `db` (pg16/pgvector), `backend` (:8000), `frontend` (:5173). `app_env=local`, dev secrets resolved (forgeable). |
| **Posture** | Read-only on the real `oneai` DB and the running uvicorn. All DDL ran only against uniquely-named throwaway scratch DBs, each dropped. No writes under `backend/` or `frontend/`. Demo admin (`super@ethera.ai`) untouched. |

---

## 1. Headline

**Both shipped controls hold for their stated contracts — and the validation found two Low, beyond-contract hardening gaps.**

- **The boot gate fails closed on every adversarial env path.** `production`, `staging`, a typo'd env, and a trailing-space `'Production '` all **RAISE** `InsecureConfigurationError` while a dev-default secret is in place; `local`/`test`/`LOCAL` still boot. Proven on the **real env-var boot path** (not just constructor kwargs): an injected `APP_ENV=production` overrides the container's `.env app_env=local` with **no fail-open fallback** to the dev exemption (exit code 1).
- **The RLS invariant test has real teeth.** It **runs, not skips** on the migrated dev DB; it dynamically enumerates exactly `{users, support_grant}`; both anti-vacuity guards fire; and a **faithfully-reproduced forgotten policy** on a tenant table turns the real test **RED** (`AssertionError: support_grant: missing the 'org_isolation' RLS policy`).

> **Scope sentence (read before citing this doc):** this validates the **boot gate** + the **invariant test**. It does **not** validate runtime row-filtering enforcement — DB-level RLS remains **inert by design** (the app connects as superuser/owner `oneai`; `FORCE ROW LEVEL SECURITY` + a non-superuser role are deferred to migration `0007`). "Invariant done" means *the policy is defined and a test guards that it stays defined* — **not** "cross-tenant is now closed at the DB layer." Suite C proves this boundary once.

### The two NEW findings (both Low, both BEYOND the stated contract)

| # | Finding | Why it's beyond-contract | Severity |
|---|---|---|---|
| **TC-SG-006** | **Blank prod secret bypasses the exact-match denylist.** The gate rejects the *two known dev-default strings*; a blank `JWT_SECRET=' '` is `!= default`, so it **boots in production** and signs a token an attacker forges by guessing `' '`. | The gate's *literal* contract is "reject the known dev defaults"; its *purpose* is "no forgeable prod signing key." Blanks slip the former while violating the latter. | Low |
| **TC-SG-015** | **The invariant test's sentinel is its own blind spot.** `_SENTINEL_TABLE='users'` is the skip-oracle ("no `org_isolation` on `users` ⇒ assume non-migrated ⇒ skip"). A forgotten policy on **`users` specifically**, on an otherwise-migrated DB, makes the test **SKIP, not FAIL**. | The test catches a forgotten policy on every tenant table *except* the one it uses as the migration marker. | Low |

**No `❌ Fail` / no `REFUTES-FIX`.** Neither control breaks its own contract; both NEW items are hardening opportunities the controls never claimed to cover.

---

## 2. What landed (the controls under test)

**Control 1 — fail-closed secrets gate (`config.py`, `f8a4fbd`).** `requires_secure_secrets` is `True` for every `app_env` outside `{local, test}`; the `_forbid_insecure_defaults_outside_dev` model-validator then raises a hard boot failure if `jwt_secret` or `postgres_password` is still the known dev default. This generalizes the prior **production-only** gate, which let `staging` / a typo'd env boot on the public dev secret.

**Control 2 — RLS standing-invariant test (`test_rls_invariants.py`, `f8a4fbd`).** Dynamically enumerates every `TenantMixin` subclass and asserts each table has `ENABLE ROW LEVEL SECURITY` + an `org_isolation` policy (migrations `0003`/`0006`), with two anti-rot guards (non-empty enumeration; content-blind platform tables stay out). `FORCE` is intentionally **not** asserted yet — it lands with `0007`.

---

## 3. Results (18 cases)

Legend: ✅ defense held · ⚠️ pass-with-concern · 🆕 NEW · ✔ CONFIRMS-FIXED · 📋 CONFIRMS-DOCUMENTED

### Suite A — JWT fail-closed gate (7)

| ID | Case | Result | Tag |
|---|---|---|---|
| TC-SG-001 | Positive control (kwarg): prod+dev-JWT names `JWT_SECRET`; inverse names `POSTGRES_PASSWORD` | ✅ | ✔ |
| TC-SG-002 | **End-to-end env-var boot** fails closed; injected `APP_ENV=production` overrides `.env` (exit 1) | ✅ | ✔ |
| TC-SG-003 | Staging generalization: `staging`+dev-JWT RAISES (the headline gap closed) | ✅ | ✔ |
| TC-SG-004 | Typo / unknown env fails closed, incl. trailing-space `'Production '` (`.lower()` not `.strip()`) | ✅ | ✔ |
| TC-SG-005 | Exempt envs preserved: `local`/`test`/`LOCAL` boot with dev secrets | ✅ | ✔ |
| **TC-SG-006** | **NEW HUNT — blank prod secret bypasses the denylist → forgeable token** | ⚠️ | 🆕 Low |
| TC-SG-007 | Whitespace foot-gun: `' local'` RAISES (safe/fail-closed but an operator ergonomics trap) | ⚠️ | — Info |

### Suite B — RLS invariant test: teeth + live-catalog truth (8)

| ID | Case | Result | Tag |
|---|---|---|---|
| TC-SG-010 | Invariant test **runs with teeth** on the migrated dev DB (`-rs` shows 0 skipped) | ✅ | ✔ |
| TC-SG-011 | Live catalog: `relrowsecurity=t`, `relforcerowsecurity=f`, `org_isolation` qual correct on both | ⚠️ | 📋 |
| TC-SG-012 | Enumeration completeness: every discovered tenant table has live RLS + policy | ✅ | ✔ |
| TC-SG-013 | **Faithful teeth proof**: forgotten policy on `support_grant` turns the REAL test RED | ✅ | ✔ |
| TC-SG-014 | Skip-on-non-migrated: LOUD skip on a pre-migration DB (no false pass) | ✅ | ✔ |
| **TC-SG-015** | **NEW — sentinel blind spot: forgotten policy on `users` SKIPs instead of FAILs** | ⚠️ | 🆕 Low |
| TC-SG-016 | Anti-vacuous guard A (import-vacuity): empty enumeration → `assert tenant_tables` fires | ✅ | ✔ |
| TC-SG-017 | Anti-vacuous guard B (content-blindness): stray `TenantMixin` on `audit_log` → `isdisjoint` fires | ✅ | ✔ |

### Suite C — RLS still inert at runtime (3, prove-once)

| ID | Case | Result | Tag |
|---|---|---|---|
| TC-SG-020 | **Superuser app role bypasses `org_isolation`**: GUC pinned to org A, org-B rows still visible (2) | ⚠️ | 📋 |
| TC-SG-021 | Forged dev-secret platform token → HTTP 200 cross-org metadata; unknown-secret control → 401 | ⚠️ | 📋 |
| TC-SG-022 | Boundary statement: `f8a4fbd` is a prod **boot-time** control, not a runtime RLS enabler | ⚠️ | 📋 |

---

## 4. The two NEW findings (detail + remediation)

### TC-SG-006 — blank prod secret bypasses the exact-match denylist (Low)

The gate (`config.py:120-127`) is `value == default` — an **exact-match denylist** of the two known dev-default strings. A blank secret is not one of them, so it boots in production.

```
[006a-prod-EMPTY-jwt]  Settings(app_env='production', jwt_secret='')  → BOOTED (no raise)
[006b-prod-SPACE-jwt]  Settings(app_env='production', jwt_secret=' ') → BOOTED (no raise)
--- forgery with the booted ' ' secret (identity/security/tokens.py:60 signs HS256 with settings.jwt_secret) ---
forged token decodes: {'sub':'attacker','org_id':'victim-org'}; attacker re-signs with guessed ' ' → verify=True
--- literal '' wrinkle (documented honestly) ---
jwt.encode(payload, '', HS256) → InvalidKeyError: HMAC key must not be empty   # crash-at-issuance, NOT forgery
raw HMAC-SHA256 empty-key round-trip → verify=True                            # the class is mathematically valid; PyJWT just guards ''
```

**Why Low, and why honest:** `''` is a *crash-at-issuance* (availability), not a silent forgery — PyJWT refuses to sign with an empty key. `' '` is the realistic forgeable bypass. Both require an **operator to deploy a blank secret** (misconfiguration / a secret manager resolving empty). The gate never *claimed* strength validation, so this is a hardening gap, not a contract violation.

**Not already covered:** `backend/tests/core/test_config.py` has no empty/blank case (only known-default-rejection, secure-boots, staging/typo); `FIX_BEFORE_PROD.md:46` covers rotating the *known dev default* ("the guard only stops the *default* from shipping") — it does not address the blank-secret class.

**Remediation:** add a **non-empty + minimum-length (≥32 bytes)** check to the gate, applied whenever `requires_secure_secrets` — turns the exact-match denylist into a minimal strength gate. Weak-but-nonempty secrets (`changeme`, `x`) boot identically and fold into the same item (no separate finding — there's no defensible entropy threshold to assert independently).

### TC-SG-015 — the invariant test's sentinel is its own blind spot (Low)

`test_rls_invariants.py:107-111` skips when `org_isolation` is absent on `_SENTINEL_TABLE='users'`, intending to skip on a non-migrated DB. But it uses "`users` has no policy" as the proxy for "DB not migrated" — so on a **fully-migrated** DB where only the `users` policy is dropped/forgotten, the proxy misfires.

```
[scratch DB oneai_sg_sentinel_*, alembic upgrade head, then DROP POLICY org_isolation ON users]
test_every_tenant_table_has_rls_enabled_and_isolation_policy  SKIPPED
SKIPPED [1] ...:108: RLS policies are migration-only and absent on this database (fresh create_all DB)...
# DB is fully migrated; only the users policy was removed — yet the test reads it as "non-migrated" and SKIPS.
```

Contrast TC-SG-013: dropping the policy on the **non-sentinel** `support_grant` correctly goes RED. So the one tenant table used as the migration sentinel is the only one whose forgotten policy escapes the teeth. **Reproduced three times** (Suite B + the independent verify agent's own scratch DB).

**Remediation:** make the skip gate read a **migration-independent** signal (e.g. `alembic_version`, or table-existence vs. policy-existence as distinct checks) so the sentinel table's *own* policy is asserted rather than used as the skip oracle. **Narrow trigger** (the `users` policy specifically missing on a migrated DB) and DB-level RLS is inert today anyway → Low.

---

## 5. CONFIRMS-DOCUMENTED — the boundary of "done" (Suite C, proven once)

DB-level org isolation is **inert at runtime**, exactly as `docs/rls-jwt-enforcement-plan.md` and `FIX_BEFORE_PROD.md:61` document — proven once here, not dwelt on:

```
TC-SG-020: current_user=oneai, rolsuper=t, rolbypassrls=t; relforcerowsecurity=f.
  BEGIN; set_config('app.current_org_id','<orgA=globex>',true);
  SELECT count(*) FROM users WHERE org_id='<orgB=demo>'  → 2   (org-B rows visible despite GUC pinned to org A)
  SELECT count(*) FROM users                              → 4   (both orgs fully visible); ROLLBACK
TC-SG-021: forged platform token (random sub, signed with the public dev secret) → GET /platform/orgs = 200,
  2 orgs of METADATA only (id/name/slug/status/user_count/created_at — no tenant content, per the platform contract).
  Negative control: same claims signed with an UNKNOWN secret → 401 "Access token is invalid." (isolates the cause to the dev secret)
```

**The precise boundary:** `f8a4fbd` *prevents* staging/production/a typo'd env from **booting** while the dev secret/password is unchanged — a real, valuable fail-closed prod control. It does **not** enable runtime RLS, and it changes nothing for the running dev stack (still `app_env=local`, still superuser `oneai`). Cross-org reads remain open at runtime until migration `0007` lands the `oneai_app`/`oneai_global` role split + `FORCE ROW LEVEL SECURITY`. The two controls are **orthogonal** and both documented.

---

## 6. Limitations / scope

- **Not validated:** live RLS row-filtering enforcement (inert pre-`0007`); the full forged-token *write* blast radius (suspend/legal-hold/break-glass-approve/erasure-403) — covered by prior audits (TC-OL-024, TC-BG-003, TC-ER-032/033) and `FIX_BEFORE_PROD.md:46`, unchanged by `f8a4fbd`.
- **TC-SG-006 forgery** was proven at the **library + config layer** (PyJWT/HS256 against the same `tokens.py:60` signing path), not by minting a token through a live `/auth` endpoint on a blank-secret-configured server — that would require restarting the running container, which the non-destructive constraint forbids. The signing layer is the actual signing layer.
- **Weak-but-nonempty secrets** are deliberately *not* a separate case (no defensible threshold) — folded into TC-SG-006's remediation.
- **Structural blind spot the invariant test cannot cover** (by design, advisor-flagged): it guards "a table that opted into `TenantMixin` forgot its policy," but is blind to the inverse — "a table that *should* be tenant-scoped but forgot to mix in `TenantMixin` at all." The enumeration only sees tables that already opted in. Partially covered by the manual checklist `FIX_BEFORE_PROD.md:63`; no automated guard.

---

## 7. Recommended follow-ups (candidates for `FIX_BEFORE_PROD.md`)

1. ~~**Harden the secrets gate**~~ — **DONE 2026-06-02.** `config.py` `_forbid_insecure_defaults_outside_dev` now rejects any non-dev `JWT_SECRET` that is blank/whitespace/under `_MIN_JWT_SECRET_BYTES=32` (RFC 7518 HS256 floor); 5 new `test_config.py` cases + live re-verification (empty/`' '`/31-byte RAISE, 32-byte boots); full suite green (197 passed). Closes TC-SG-006 (subsumes the weak-secret class). No floor on `POSTGRES_PASSWORD` (blank can be legitimate IAM/peer/cert auth). *Extended the "Rotate `JWT_SECRET`" item.*
2. **Fix the invariant-test sentinel** — skip on a migration-independent signal so a forgotten `users` policy FAILs rather than SKIPs (closes TC-SG-015). *Extends the per-table RLS rule.*
3. *(Ergonomics, optional)* `.strip()` `app_env` so a padded `' local'` doesn't produce a cryptic secrets boot-failure (TC-SG-007) — purely operator-facing; the current behavior errs safe.
