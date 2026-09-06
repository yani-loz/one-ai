# Platform Console (`/platform/*`) — Dynamic Adversarial Stress & Validation

> **Status as of 2026-09-06 (dated record — the findings below are unchanged):** two of this pass's findings have since been closed in code.
> **F-01 — the RLS half is closed.** The root cause recorded at §3 (*"With RLS inert (the app connects as Postgres superuser `oneai`, `database.py:32`)"*) no longer holds: `backend/app/core/database.py:59-77` now runs a four-role split — `oneai_app` (write plane, NOSUPERUSER, NO BYPASSRLS, the role RLS enforces against), `oneai_reader` (SELECT-only person-bound read plane, also NO BYPASSRLS) and `oneai_global` (BYPASSRLS, cross-org/pre-org flows only) — and RLS is ENFORCED since migration `0009_enforce_rls.py`, with 22 tables carrying ENABLE + FORCE + an `org_isolation` policy on the live dev DB, measured 2026-09-06 (`docs/audits/2026-09-06_built-vs-docs-map.md` §3). F-01's **other half — the forged dev-secret token — is still open** (`docs/FIX_BEFORE_PROD.md:46`, unchecked).
> **N-04 is closed.** Both login schemas now use the bounded `LoginPassword` type (`backend/app/identity/schemas/user_schemas.py:51-53` — `min_length=1`, `max_length=128`, plus a ≤72 UTF-8 byte bcrypt cap) instead of `Field(min_length=1, max_length=256)`: `auth_schemas.py:28` and `platform_schemas.py:36`.
> **§7 proposed `FIX_BEFORE_PROD.md` updates:** rec 1 applied (`docs/FIX_BEFORE_PROD.md:48` now names the N-01 / TC-PC-073 bcrypt CPU-amplification vector and the pre-bcrypt throttle) · rec 2 applied (`:49`, the pool + worker sizing item citing N-02 / TC-PC-071) · rec 3's substance shipped in **code** (the `LoginPassword` byte cap above), though the password-policy item at `:54` was never amended to mention it · rec 4 **not applied**, and the AUD-06 family-revocation deferral it targets is still open at `:55`.

> **Scope:** the Platform Console backend (module `PC`, epics PC-01/PC-02) — the separate
> platform auth domain (`aud='platform'`): `GET /platform/me`, `POST /platform/refresh`,
> `POST /platform/logout`, `POST /platform/orgs`, `GET /platform/orgs`, `POST /platform/login`.
> Dynamic, adversarial testing against the **live stack** (real uvicorn `:8000`, Postgres `:5432`),
> complementing the static reviews `2026-06-01_platform-console-pr1-review.md` (22 findings) and
> `2026-06-01_platform-session-pr2-review.md` (4 findings).
>
> **Method:** an 8-agent `Workflow` authored + executed + recorded **54 test cases** in three
> phases (6 functional suites in parallel on a quiet pool → RACE alone → STRESS alone, so load
> couldn't pollute correctness). Each case ran inside the backend container against real uvicorn
> via `cat _common.py tc_NNN.py | docker compose exec -T backend python -`, with psql ground-truth
> on the db container. The lead **independently re-verified every headline finding first-hand**
> before this writeup. ~939k agent tokens, 404 tool calls.
>
> **Suite artifacts:** `testing/02_platform-console/` (one `TC-PC-NNN_*.md` per case with raw
> evidence + the reproducible `harness/tc_NNN.py`). Status dashboard: that folder's `README.md`.

## 1. Executive summary

**54 cases · 49 ✅ pass · 3 ⚠️ pass-with-concern · 2 ❌ fail (the documented forged-secret win) · 0 REFUTES-FIX.**
The PC-01/02 implementation is **solid**: every audience/subject_type confinement, token-validation,
single-use-rotation, onboarding-integrity, content-blindness, and account-lifecycle contract held
**live and discriminatingly**. The STRESS phase found **zero 5xx and zero client errors** at up to
200× concurrency and under bcrypt saturation — no availability defect.

The only "wins" are:
- **2 ❌ FAILs (CONFIRMS_DOCUMENTED)** — a forged dev-secret platform token grants full platform
  power (onboard + list all customers). This is the tracked *Rotate JWT_SECRET* / *Enforce RLS*
  deferral, now demonstrated live and dramatically.
- **5 🆕 NEW observations, all Low/Info** — defense-in-depth gaps, no live Critical/High defect.

No prior fix regressed. The PR-2 review's `test-1` false-green (cross-domain tests that didn't
discriminate the guard) is **closed dynamically**: TC-PC-020/026 prove the audience check is the sole
load-bearing reason for the 401.

## 2. Results by suite

| Suite | Cases | ✅ | ⚠️ | ❌ | 🆕 |
|---|---|---|---|---|---|
| PSES — session lifecycle (me/refresh/logout, deactivated account) | 8 | 8 | | | |
| PLOGIN — login negatives (enumeration, inactive, bounds) | 6 | 5 | 1 | | 1 |
| XDOM — cross-domain confinement ⭐ | 7 | 5 | | 2 | |
| PAZ — token-validation matrix | 9 | 9 | | | |
| ONB — onboarding contracts + input fuzz | 9 | 8 | 1 | | |
| CB — content-blindness | 4 | 4 | | | |
| RACE — concurrency races | 6 | 6 | | | 2 |
| STRESS — pool exhaustion + bcrypt saturation | 5 | 4 | 1 | | 2 |
| **Total** | **54** | **49** | **3** | **2** | **5** |

## 3. The documented deferral, demonstrated live (the ❌ wins)

### F-01 · Forged dev-secret platform token = full platform takeover — Critical (tracked)
*Cases TC-PC-024 (write), TC-PC-025 (read). Tag: CONFIRMS_DOCUMENTED. Lead-re-verified.*

A platform access token forged with the **known dev `JWT_SECRET`** and a **random, non-existent
`sub`** is accepted on every protected platform endpoint:
- `POST /platform/orgs` → **201**: onboarded a real org + `company_admin`, committed to the live DB
  (psql-verified rows present).
- `GET /platform/orgs` → **200**: returned the **entire fleet** (lead re-verify: **23 orgs**,
  including the seeded `demo`/`globex` customers) — cross-customer metadata exposure.

**Root cause:** `get_current_platform_admin` (`dependencies.py:103-117`) verifies only signature +
`aud` + expiry; it never checks the admin exists (correct by design — `/me` does that downstream).
With RLS inert (the app connects as Postgres superuser `oneai`, `database.py:32`), **the JWT secret
is the single isolation layer**. The dev default `'dev-only-insecure-secret-change-me-in-prod'` is
public in-repo, so any party can mint a valid platform token.

**Blast radius is bounded by content-blindness** (TC-PC-025 confirmed the response was exactly the 6
metadata fields — no tenant content), so the read exposure is org existence + headcount, not
conversations/memory. The write exposure (arbitrary onboarding) is unbounded.

**Tracked:** `FIX_BEFORE_PROD.md` → *Rotate `JWT_SECRET`* (+ fail-boot-if-default guard) and
*Enforce the (already-defined) RLS policy*. Closing either de-fangs this; closing both is the bar.
Recorded as ❌ FAIL because the contract "only a real platform admin can onboard/list" is violated
live — the value is the empirical proof, not a new gap.

## 4. New findings (all Low / Info — no live Critical/High defect)

### N-01 · bcrypt CPU-amplification DoS via unauthenticated invalid logins — Low
*Case TC-PC-073 (⚠️). Lead-re-verified: invalid 322 ms ≈ valid 320 ms single-request.*

`/platform/login` (and `/auth/login`) run a full bcrypt (`rounds=12`) verification on **every**
attempt, including unknown emails — `DUMMY_PASSWORD_HASH` (`security/password.py:60`) deliberately
pays the cost so timing can't enumerate accounts. Correct for anti-enumeration, but it also means an
**unauthenticated** attacker pins the bounded anyio bcrypt threadpool with junk credentials: 40
concurrent invalid logins → median **10.7 s**, all 401, no 500. The equalizer must **not** be
removed (it defends N/A enumeration). **Fix:** a per-IP throttle in *front* of bcrypt.
`FIX_BEFORE_PROD.md`'s rate-limit item exists but its rationale names only credential-stuffing/brute-
force — **widen it to name this CPU-amplification vector**.

### N-02 · DB connection-pool + worker sizing is unconfigured and untracked — Info
*Case TC-PC-071 (✅).*

`create_async_engine(...)` (`database.py:32`) sets no `pool_size`/`max_overflow`, so SQLAlchemy
defaults apply: **5 + 10 = 15 connections, `pool_timeout=30s`**. Behaviour is *graceful* today — no
knee to 200× concurrency (every level 100% 200; median latency 388→1226 ms, well inside the 30 s
queue) — but capacity is unsized and **absent from `FIX_BEFORE_PROD.md`**. Latency grew ~10× faster
than a pure pool-queue model predicts → **single-event-loop CPU serialization** (JWT decode + ORM +
Pydantic) is a co-bottleneck, so the ceiling isn't solely the pool. **Fix:** add an Ops item to size
the pool + uvicorn workers deliberately for the target load.

### N-03 · Logout does not revoke the refresh-token *family* under a refresh race — Low
*Case TC-PC-063 (✅). Lead-re-verified: rotate R0→R1, logout(R0)=204, R1 refresh=200.*

`logout` revokes only the **presented** token hash (`token_rotator.py:66-68`). If a refresh races
the logout and wins, the descendant token it mints survives the logout — the user "logged out" but a
live session token remains. Same class as the tracked **AUD-06** reuse-family deferral. Server never
500s; the presented token always dies. **Fix:** folds into AUD-06 (revoke the
`subject_id+subject_type` family) — needs the independent-commit/`audit_log` work already noted there.

### N-04 · Login password field lacks `min_length`/byte bounds (defense-in-depth asymmetry) — Low
*Case TC-PC-013 (⚠️). Lead-re-verified: 7-char→401, 100-char→401 (not 500), 300-char→422.*

`PlatformLoginRequest.password` and `LoginRequest.password` are `Field(min_length=1, max_length=256)`
(`platform_schemas.py:34`, `auth_schemas.py:28`) — **not** the `BcryptPassword` type (with
`min_length=8` + 72-byte cap) used by user-create. No live defect: `verify_password` swallows
bcrypt's >72-byte `ValueError` → 401 (`security/password.py:51-54`). But the login surface has no
input bound besides 256 chars; if that downstream `try/except` were ever removed, the unbounded field
becomes a 500 oracle. **Fix:** overlaps the *password policy* item — add a byte cap at the login
schema boundary for symmetry. (Corrects the test brief's wrong assumption that login uses
`BcryptPassword`.)

### N-05 · Concurrent issuance characterization — informational (no defect)
*Case TC-PC-064 (✅).* 30 concurrent `/platform/login` → 30/30 200, all access+refresh tokens and
their sha256 hashes distinct. Recorded for completeness; distinctness floor is cryptographic
(`secrets.token_urlsafe`). No action.

## 5. Empirical verdicts on prior claims (what held)

- **PC-02 acceptance criteria — all proven live** (strictly stronger than the unit tests the epic
  cites): AC1 (TC-002/003/060), AC2 (TC-001), **AC3a discriminating** (TC-020 + role-hardened
  TC-026), **AC3b reject-without-revoke** (TC-021), AC4 (TC-003/004/065), AC7 (TC-007 deactivated +
  TC-008 unknown), AC8 (TC-030). The PR-2 `test-1` false-green is closed.
- **Token-validation gate is total** (PAZ 9/9): missing-bearer→401-not-403, `alg=none`, tampered
  signature (discriminating control), expired (distinct "expired" detail), wrong secret, missing
  `exp`/`aud`/`sub`, non-UUID `sub`, garbage bearer → all **401, never 500**; gate present on every
  platform endpoint with a psql no-write proof.
- **Onboarding integrity** (ONB + RACE): dup slug/email→409; slug-pattern, password byte-limit
  (30-emoji/120-byte→422 not bcrypt-500), `extra='forbid'`, NUL→422, SQL-injection stored literally
  (users table intact), email canonicalization — all held. **Concurrent same-email onboarding (50×)
  → exactly 1 org + 1 user, ZERO orphans** (TC-062, run-stamp-filtered psql): the atomic rollback
  holds under real contention.
- **Content-blindness** (CB): `/orgs` exactly 6 metadata fields, `/me` exactly 3, onboard response
  withholds the hash — all **non-vacuous** (the withheld columns physically exist on the resolved
  rows). *Limitation:* content/cost/token columns don't exist yet (Connect/Ask/Learn unbuilt), so
  their absence is currently vacuous — **re-run when the first tenant-content table lands.**
- **No-enumeration** (PLOGIN): wrong-password vs unknown-email byte-identical body + comparable
  timing; inactive admin folds into the same generic 401 (no account-status leak).

## 6. Coverage & limitations

- **Backend only.** The PC-01 **frontend** console (role-guards as UX-only, in-memory token never in
  `localStorage` / sec-1, single-flight refresh on 401, half-open teardown, stored-XSS render of
  `full_name`/`org_name`) is a **separate Playwright pass** (Target 04) — not covered here.
- **Same-row races are corroboration, not proof.** TC-060/065 (single-use on one token) serialize on
  the row lock by design; clean results corroborate the conditional-revoke (confirmed by code review
  of `token_rotator.consume`), but the independently-provable races are the **different-row** onboard
  collisions (TC-061/062), which leave UNIQUE-violation artifacts and were the positive control.
- **Content-blindness is forward-fragile** (see §5) — schedule a re-run when tenant-content tables exist.
- **Shared dev DB.** All data was run-stamped; psql ground-truth always filtered by stamp, never a
  global `COUNT(*)`. The pre-flight confirmed no concurrent actor.

## 7. Recommended `FIX_BEFORE_PROD.md` updates (proposed — not yet applied)

1. **Widen the login rate-limit item** to name the bcrypt CPU-amplification / unauthenticated-DoS
   vector (N-01) — per-IP throttle in front of bcrypt.
2. **Add an Ops item:** size the DB connection pool + uvicorn workers for the target load (N-02);
   the engine currently ships SQLAlchemy defaults.
3. **Add a byte cap at the login password schema boundary** for parity with `BcryptPassword` (N-04),
   under the existing *password policy* item.
4. **Cross-reference N-03** under the AUD-06 family-revocation deferral (logout, not just reuse,
   leaves a descendant alive under a race).

The forged-token win (F-01) is already fully tracked (*Rotate `JWT_SECRET`* + *Enforce RLS*) — no new
item, but this audit is the live evidence those two gates are real, not theoretical.

## 8. Post-run state (for cleanup)

- Demo platform admin `super@ethera.ai` — **untouched** (verified `is_active=t`).
- Throwaway pool (`tw-*-tw06012c3@oneai.dev`): `tw-active`/`tw-inactive` intact;
  **`tw-lifecycle` left DEACTIVATED** by TC-PC-007 (token-outlives-account, by design).
- Run-stamped test data remains: ~20+ orgs (`xdom-*`, `onb*-*`, `cb05*-*`, `race061/062-*`,
  `stress-rec-*`, `probe-*`) + their users + refresh-token rows, incl. **2 forged-token orgs**
  (TC-024) and the lead probe org. A `TRUNCATE` of the identity tables + re-seed restores a clean
  demo (`docker compose exec backend uv run python -m scripts.seed_identity`).
- **Update (2026-06-01, observed during the Target 04 FE pass):** the dev DB was **re-seeded to the
  clean demo+globex state** by another session (fleet dropped 23 → 2 orgs), so the run-stamped + forged
  rows listed above are **already gone**; this audit's `.md` + harness evidence still stands. The FE pass
  then added one org `xss-render-test` (markup-name XSS test, TC-FE-005) which now remains.
