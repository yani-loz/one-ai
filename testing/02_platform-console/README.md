# Target 02 — Platform Console (`/platform/*`) — Adversarial Stress & Validation

> Dynamic, adversarial validation of the **Platform Console** backend (module `PC`) against
> the **live stack** — break it, prove cross-domain confinement, fire concurrency races,
> exhaust the connection pool, fuzz inputs, and empirically (re)prove the PC-02 acceptance
> criteria. Companion to the static reviews in `docs/audits/2026-06-01_platform-*-review.md`
> and the PM epics `docs/PM/platform-console/EPIC-PC-01..02`.
>
> See `testing/README.md` for the strategy, legend, and finding tags. Methodology:
> `.claude/skills/adversarial-validation/`.

## Scope

The super-admin / governance control plane — the **separate** auth domain (`aud='platform'`).

**In scope (this pass — backend):**
- **Session lifecycle (PC-02):** `GET /platform/me`, `POST /platform/refresh`,
  `POST /platform/logout` — single-use rotation, idempotent logout, reuse-after-logout,
  token-outlives-account.
- **Cross-domain confinement (⭐ non-negotiable):** company token rejected on `/platform/*`
  and vice-versa — proven **discriminatingly** (the audience/subject_type guard is the only
  thing preventing success), closing the test-1 confidence gap from the PR-2 review.
- **Onboarding (`POST /platform/orgs`):** duplicate slug/email → 409, atomic rollback (no
  orphan org), validation bounds, slug pattern, response shape.
- **Content-blindness:** `GET /platform/orgs` returns the 6 metadata fields only;
  `GET /platform/me` returns id/email/full_name only (never the password hash).
- **AuthZ / token validation:** missing bearer → 401 (not 403), `alg=none`, tampered,
  expired, wrong-audience, malformed claims → 401 (not 500), unknown/deactivated admin.
- **Login negatives:** wrong-password vs unknown-email byte-identical (no enumeration via
  the `DUMMY_PASSWORD_HASH` timing defense), inactive admin, overlong password → 422.
- **Concurrency races:** platform refresh single-use under contention; concurrent same-slug
  / same-email onboarding (different-row UNIQUE → 409, the independently-provable race +
  positive control).
- **Stress / load:** connection-pool exhaustion (engine sets no explicit `pool_size`, so the
  SQLAlchemy async defaults **5 + 10 overflow = 15** apply, `pool_timeout=30s`); bcrypt-bound
  `/platform/login` saturation (`rounds=12`).

**Out of scope (separate passes):**
- The PC-01 **frontend** console UI (role guards, in-memory token, single-flight refresh,
  stored-XSS render) → Playwright pass, Target 04.
- Company-user management (`/users/*`) and infra — covered by Target 01.

## Environment

- Live stack: `docker compose up` → API `:8000` (`/docs`, `/health`), db `:5432`.
- Harness scripts run **inside the backend container against real uvicorn**, self-contained
  over stdin (the `testing/` tree is not volume-mounted):
  `docker compose exec -T backend python - < testing/02_platform-console/harness/<script>.py`
- psql ground-truth runs against the **db** container (backend has no psql):
  `docker compose exec -T db psql -U oneai -d oneai -c "<SQL>"`.
- Demo platform admin: `super@ethera.ai` / `Sup3r-Dev-Only-2026!` — used only to onboard
  fresh orgs; **never mutated**.

## Key facts (the levers this pass pulls)

- **RLS is defined-but-inert** → the JWT secret is the single isolation layer; the dev secret
  is the forgeable default, so forged tokens are a real capability.
- **Refresh single-use** is a conditional `UPDATE ... WHERE revoked_at IS NULL` (atomic) —
  same-row races serialize on the row lock *by design*, so a black-box pass there is
  **corroboration confirmed by code review**, not independent proof (contrast the onboard
  different-row race, which leaves a UNIQUE-violation artifact).
- **Domain mismatch on refresh rejects WITHOUT revoking** (presenting a foreign token must
  not let a caller DoS someone else's session) — the AC3b property.

## Status dashboard

> Result: ⬜ not run · ✅ pass (defense held) · ❌ fail (a defect — the win) · ⚠️ pass-with-concern.
> Tag: 🆕 NEW · ✔ CONFIRMS-FIXED · ✖ REFUTES-FIX · 📋 CONFIRMS-DOCUMENTED · — n/a.
> **Run 2026-06-01** (8-agent workflow + lead re-verification). Consolidated audit:
> [`docs/audits/2026-06-01_platform-console-dynamic-adversarial.md`](../../docs/audits/2026-06-01_platform-console-dynamic-adversarial.md).

**54 cases · 49 ✅ · 3 ⚠️ · 2 ❌ (both the documented forged-secret win) · 0 REFUTES-FIX · 5 🆕 (all Low/Info).**
**Zero 5xx and zero client errors across the entire stress phase — no availability defect.**

| Suite | Cases | Result spread | NEW | Notes |
|---|---|---|---|---|
| PSES — session lifecycle | 8 | 8 ✅ | — | me/refresh/logout, reuse, deactivated-account all held (PC-02-AC1/4/7). |
| PLOGIN — login negatives | 6 | 5 ✅ · 1 ⚠️ | 1 | TC-013 🆕 Low: login password field has only `max_length=256` — no `min_length=8` / 72-byte cap (no live defect; downstream `try/except` absorbs). |
| XDOM — cross-domain ⭐ | 7 | 5 ✅ · 2 ❌ | — | Discriminating both directions held (AC3a/3b). **TC-024/025 ❌ = forged dev-secret token onboards + lists all orgs** → 📋 *Rotate JWT_SECRET*. |
| PAZ — token validation | 9 | 9 ✅ | — | alg=none/tampered/expired/wrong-secret/missing-claims/malformed/garbage → 401, never 403/500. Gate on every endpoint (psql no-write proof). |
| ONB — onboarding + fuzz | 9 | 8 ✅ · 1 ⚠️ | — | dup→409, slug/pw/extra/NUL/injection/email-canon all held. TC-043 ⚠️: no-orphan confirmed but rollback path is concurrency-only (→ RACE TC-062). |
| CB — content-blindness | 4 | 4 ✅ | — | `/orgs` 6 fields, `/me` 3 fields, no hash (non-vacuous: data exists on row). Caveat: content columns unbuilt → re-run when Connect/Ask/Learn land. |
| RACE — concurrency | 6 | 6 ✅ | 2 | Single-use (same-row, corroborated), same-slug & same-email onboard races held with **zero orphans** (psql, run-stamp-filtered). TC-063 🆕 Low: logout ≠ token-family revoke (AUD-06 class). TC-064 🆕: issuance characterization (no defect). |
| STRESS — pool + bcrypt | 5 | 4 ✅ · 1 ⚠️ | 2 | No knee to 200 concurrent; recovery clean, no leak. TC-071 🆕 Info: DB pool unconfigured (defaults 15) + absent from FIX_BEFORE_PROD. TC-073 🆕 Low: bcrypt CPU-amplification DoS via invalid logins. |

### Headline findings (lead-re-verified first-hand)

| ID | Sev | Result | Finding |
|---|---|---|---|
| TC-PC-024/025 | Critical (tracked) | ❌ 📋 | Forged dev-secret platform token (random `sub`) onboards a real org **and** lists all 23 orgs' metadata — the JWT secret is the single isolation layer (RLS inert). Tracked: *Rotate JWT_SECRET* + *Enforce RLS*. |
| TC-PC-073 | Low | ⚠️ 🆕 | bcrypt `rounds=12` runs on every login incl. unknown-email (DUMMY_PASSWORD_HASH); 40 concurrent invalids → median 10.7 s, all 401, no 500. Unauthenticated CPU-amplification DoS surface — the rate-limit item names only brute-force. |
| TC-PC-071 | Info | ✅ 🆕 | Async engine sets no `pool_size` → SQLAlchemy defaults (15 conns); no knee to 200 concurrent (graceful), but pool/worker sizing is unconfigured and untracked. CPU serialization is a co-bottleneck. |
| TC-PC-063 | Low | ✅ 🆕 | Logout revokes only the presented refresh-token hash, not the subject's token family; a descendant minted by a racing refresh survives logout (same class as tracked AUD-06). |
| TC-PC-013 | Low | ⚠️ 🆕 | `/platform/login` + `/auth/login` password is `Field(min_length=1, max_length=256)`, not `BcryptPassword` — a defense-in-depth asymmetry vs user-create (no live defect today). |

## Coverage → PC-02 acceptance criteria (dynamically proven on the live server)

| AC | Criterion | Dynamic proof |
|---|---|---|
| PC-02-AC1 | rotation single-use via `/platform/refresh` | ✅ TC-PC-002, TC-PC-003, TC-PC-060 |
| PC-02-AC2 | `/platform/me` returns the admin's real identity | ✅ TC-PC-001 |
| PC-02-AC3a | company token rejected on `/platform/me` (**discriminating** — real-admin `sub`) | ✅ TC-PC-020, hardened by TC-PC-026 (role-vs-aud) |
| PC-02-AC3b | company refresh rejected on `/platform/refresh` **without revoking** | ✅ TC-PC-021 (then rotates at `/auth/refresh`) |
| PC-02-AC4 | refresh single-use; logout revokes | ✅ TC-PC-003/004/065; ⚠️ family-revoke gap TC-PC-063 |
| PC-02-AC7 | unknown/deactivated admin in a valid token → 401 | ✅ TC-PC-007 (deactivated), TC-PC-008 (unknown) |
| PC-02-AC8 | missing token on `/platform/me` → 401 (not 403) | ✅ TC-PC-030 |
