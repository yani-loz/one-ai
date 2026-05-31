# Identity & Access — Dynamic Adversarial Test Pass (Target 01: Infra + AuthN/AuthZ)

**Date:** 2026-05-31
**Scope:** The live Identity & Access module and its surrounding scaffold — `/health`,
request-body guard, CORS, RLS posture, company + platform authentication, token
validation, the role gate, **cross-tenant isolation**, token lifecycle, and input
validation on the auth surfaces. Tested against the **running stack**
(`docker compose` — real uvicorn `:8000` + Postgres), not the in-process ASGI app.
**Method:** 46 adversarial test cases authored from a template, each executed by a
self-contained `httpx`/`asyncio`/`pyjwt` harness inside the backend container, each
recorded with raw request/response evidence. Concurrency cases ran 30–60 iterations.
Full per-case evidence lives under [`testing/01_infrastructure-authn-authz/`](../../testing/01_infrastructure-authn-authz/).

> **This is the DYNAMIC complement to `2026-05-30_identity-module-deep-audit.md`.** That
> review was static and its Limitations section explicitly disclaimed dynamic testing
> ("no concurrency load test to observe the AUD-01/05/11 races firing"). This pass closes
> that gap: it **empirically fires the races**, **proves or refutes** the audit's fix
> claims against a live server, and surfaces defects only dynamic testing can see. Every
> result is tagged `NEW` / `CONFIRMS-FIXED` / `REFUTES-FIX` / `CONFIRMS-DOCUMENTED` so the
> signal (new defects + live verdicts) is separated from re-confirmation of known items.

---

> **⟳ Remediation status (updated 2026-05-31): all 4 NEW findings FIXED + verified.**
> - **DYN-01** — the last-admin guard now locks the active-admin set `FOR UPDATE` (ordered, deadlock-free); re-running TC-IA-050 gives `{204+409: 50}` — **0 lockouts** (was 49/50). `user_service._guard_last_admin` + `user_repository.lock_active_admin_ids`.
> - **DYN-02** — emails are canonicalized to lowercase (`NormalizedEmail`); TC-IA-064 now: 2nd create → **409**, opposite-case login → **200**, stored lowercase.
> - **DYN-03** — control chars rejected at the validation boundary (`SafeName` → 422) plus a global `DataError`→422 safety net; TC-IA-066 NUL → **422** (was 500).
> - **DYN-04** — `extra="forbid"` on the identity request models (unknown field → 422).
> All four are covered by new pytest cases (115 passed, 97.8% cov) and re-verified against the live stack. The per-case files below retain the original ❌ evidence (the historical proof) — re-run the harnesses to watch them flip to ✅.

---

## 1. Executive summary

**46 cases · 32 Pass · 5 Pass-with-concern · 9 Fail (defects reproduced) · 0 `REFUTES-FIX`.**

The module's core defenses **held under live adversarial probing**: the full token-validation
matrix (`alg=none`, tampered signature, expiry, required claims, malformed claims → all
401, never 500), the two-audience domain split (company↔platform, both directions 401),
the role gate (member → 403), cross-tenant PATCH/DELETE/LIST (→ 404 / own-org-only), and —
critically — **all three concurrency fixes the prior audit claimed (AUD-01 rotation race,
AUD-05 create/onboard races) were proven to hold under real contention.** No claimed fix
was refuted.

The pass found **3 distinct NEW defects** (not in the prior audit or `FIX_BEFORE_PROD.md`),
all independently reproduced:

| ID | Severity | NEW defect |
|---|---|---|
| **DYN-01** | Medium | **Last-admin guard is not concurrency-safe** — a non-atomic check-then-act races two concurrent admin removals to **0 active admins = org lockout**, reopening on the concurrent path the exact lockout AUD-07's serial guard closed. Fired in **48–59 of 50–60** iterations across DELETE+DELETE, PATCH+PATCH, and mixed paths. |
| **DYN-02** | Medium | **Email is never canonicalized** — `Foo.Bar@x` and `foo.bar@x` both create as **distinct users** (global-uniqueness defeated), and login is **case-sensitive on the local-part** (a user who types a different case is locked out with 401). |
| **DYN-03** | Low | **A NUL byte in `full_name` → HTTP 500** — `\x00` passes Pydantic length bounds, reaches the INSERT, and asyncpg raises `CharacterNotInRepertoireError` (a `DBAPIError`, neither `IntegrityError` nor `IdentityError`) → unmapped → opaque 500. |

It also **reproduced 6 already-documented deferrals** once each (to characterize, not
re-litigate): RLS is inert (superuser bypass — the documented single point of failure),
the chunked-body size-cap bypass, the credentialed-wildcard CORS posture, the AUD-04
cross-tenant email oracle, **forged-token cross-tenant read+write**, and the
demoted-admin "no access-token denylist" window. These are tagged `CONFIRMS-DOCUMENTED`
and tracked in `FIX_BEFORE_PROD.md`; the two forged-token cases are the live proof that,
with RLS inert, **the dev `JWT_SECRET` is the only thing standing between any caller and
every tenant's data.**

---

## 2. NEW findings (detailed)

### DYN-01 — Last-admin guard is non-atomic: concurrent admin removal strands the org at 0 admins
- **Severity:** Medium · **Category:** correctness / availability · **CWE-362/367 (TOCTOU)**
- **Cases:** TC-IA-050 (DELETE+DELETE), TC-IA-051 (PATCH→member ×2), TC-IA-052 (mixed PATCH+DELETE)
- **Location:** `UserService._guard_last_admin` (`backend/app/identity/services/user_service.py:123-145`) →
  `UserRepository.count_active_admins` (`user_repository.py:81-99`); mutations flush at
  `user_service.py:106`/`:121`, commit deferred to `get_tenant_session` (`dependencies.py:134-141`).
- **Tag:** **NEW** (not `REFUTES-FIX` — see below).

**Evidence (live).**

```
TC-IA-050  iterations=50   status-pair distribution: {'204+409': 2, '204+204': 48}
           ZERO-ADMIN (locked-out) iterations: 48     psql: active_admins=0, total_users=2
TC-IA-051  iterations=49   {'200+200': 48, '200+409': 1}   ZERO-ADMIN: 48
TC-IA-052  iterations=50   {'PATCH=200,DELETE=204': 49, 'PATCH=200,DELETE=409': 1}   ZERO-ADMIN: 49
```
Independently reproduced by the lead with a separate harness: **59 of 60** iterations →
0 active admins (`{(204,204): 59, (204,409): 1}`).

**Mechanism.** `_guard_last_admin` runs `SELECT count_active_admins(exclude=self)` (a plain
`SELECT`, no `FOR UPDATE`), then mutates and flushes, with the commit deferred to end of
request under READ COMMITTED. Two concurrent removals each exclude *their own* target and
both see the *other* admin still active (count = 1), both pass the guard, both commit →
0 admins. The two UPDATEs touch different rows, so there is no lock contention to serialize
them.

**Why NEW, not REFUTES-FIX.** AUD-07's guard (and its "FIXED" verification) closed the
*serial* lockout — a single sequential action can't drop admins to zero. The `…+409`
iterations here prove that serial guard **still holds**. What broke is a *different defect
class*: concurrency-safety (CWE-362), which the audit's Limitations section explicitly
disclaimed and never filed for the last-admin path (it filed the *other* races — AUD-01,
AUD-05 — separately). This is the last-admin analogue the audit never filed.

**Impact.** Within-tenant availability: an org whose two admins are removed near-simultaneously
(double-click, two admins acting at once, a retrying client) is locked out of its own user
management with **no in-app recovery** (members are 403 on `/users`; a self-deactivated admin
can't log back in) — recoverable only by platform admin or direct DB surgery. No cross-tenant
breach → Medium.

**Remediation.** Make the guard atomic: `SELECT … FOR UPDATE` on the candidate admins, or a
single conditional `UPDATE … WHERE` that asserts another active admin still exists, or
SERIALIZABLE isolation on the mutating transaction; treat the lost race as `LastAdminError`
(409). **The fix pattern already exists in this codebase** — AUD-01's rotation race was
closed with exactly this conditional-`UPDATE` approach (proven holding in TC-IA-053).
Recommend adding to `FIX_BEFORE_PROD.md`.

---

### DYN-02 — Email is never canonicalized: duplicate identities + case-fragile login
- **Severity:** Medium · **Category:** correctness / security · **CWE-178 (improper case handling)**
- **Case:** TC-IA-064
- **Location:** `EmailStr` (lowercases the **domain** only, preserves local-part case) feeding
  exact-match `UserRepository.email_exists` (`user_repository.py:76-79`) and
  `get_by_email` (`:38-45`); no normalization on store or compare.
- **Tag:** **NEW.**

**Evidence (live).**
```
create #1  Foo.Bar-<s>@Example.com  -> 201  stored: Foo.Bar-<s>@example.com
create #2  foo.bar-<s>@example.com  -> 201  stored: foo.bar-<s>@example.com   (distinct row)
login solo user, OPPOSITE-case local-part (FOO.BAR…) -> 401
login solo user, EXACT stored case                   -> 200
```
Independently reproduced by the lead with identical results.

**Impact.** Two facets, one root cause (no canonicalization):
1. **Uniqueness bypass / duplicate identities.** The "email is globally unique" invariant
   (relied on by login, the dup check, and the cross-tenant oracle) is defeated by case
   variation. For the vast majority of real mail systems the local-part is
   case-insensitive, so `Foo.Bar@` and `foo.bar@` are the *same human* — yet the system
   stores two separate accounts. An admin can create a confusing/duplicate account; the
   uniqueness guarantee other code leans on is weaker than it reads.
2. **Case-fragile login / lockout.** A user provisioned as `Foo.Bar@…` who later types
   `foo.bar@…` (or whose SSO/password-manager normalizes case) **cannot log in** (401).
   The domain is normalized but the local-part is not — an inconsistent, surprising auth
   path and a real support burden for a DACH enterprise product.

**Remediation.** Canonicalize email at the validation boundary (a normalized form — at
minimum lowercasing, ideally NFC + lowercase — stored in a dedicated unique column) and
compare against the canonical form on both create and login. Decide the policy explicitly
and document it; pairs with the AUD-04 email-uniqueness decision already pending in
`FIX_BEFORE_PROD.md`.

---

### DYN-03 — A NUL byte in `full_name` reaches the DB and raises an unmapped 500
- **Severity:** Low · **Category:** input validation / robustness · **CWE-20 / CWE-248**
- **Case:** TC-IA-066
- **Location:** `UserCreateRequest.full_name` validates char-count only
  (`user_schemas.py:49`); `\x00` survives to `UserRepository.add`'s INSERT
  (`user_repository.py:101-105`), where asyncpg raises `CharacterNotInRepertoireError`
  (`invalid byte sequence for encoding "UTF8": 0x00`) — a `DBAPIError`, neither
  `IntegrityError` nor `IdentityError`, so no handler maps it (`error_handlers.py`,
  `main.py`) → FastAPI default **HTTP 500**.
- **Tag:** **NEW** (same *shape* as AUD-02's unhandled-error→500 class, but a different
  field and a different trigger; not covered by AUD-02 or `FIX_BEFORE_PROD.md`).

**Evidence (live).**
```
POST /users  full_name="Eve\x00Hacker"  -> 500  "Internal Server Error"
control      full_name="Clean Name"     -> 201
psql: users table intact (no corruption); reproduced independently by the lead.
```

**Impact.** An authenticated company-admin sending a schema-valid-looking payload gets an
opaque 500 and no account, indistinguishable from a real server fault. Authenticated and
non-destructive (the transaction rolls back), so Low — but it is an un-mapped crash on a
core write path, and the same gap would swallow any other `DBAPIError` (e.g. an
encoding/constraint case the pre-checks miss) as a 500 rather than a clean 4xx.

**Remediation.** Reject control characters / NUL at the validation boundary (a
`field_validator` on `full_name`, `org_name`, etc. forbidding `\x00` and ideally other
C0 controls → 422), **and** add a global `DBAPIError`/`SQLAlchemyError` handler that maps
unexpected DB errors to a clean 400/422 instead of leaking a 500 (defense in depth).

---

### DYN-04 (Info) — `POST /users` relies on Pydantic's default unknown-field drop, no `extra="forbid"`
- **Severity:** Info (hardening) · **Case:** TC-IA-033 (behaviour **correct** — Pass-with-concern)
- A smuggled `org_id` in the create body is correctly ignored (the user lands in the
  caller's org; the service forces `org_id` from the token). The protection, however,
  rests on Pydantic silently dropping unknown fields, not on an explicit `extra="forbid"`
  guard. **Not a defect** — recorded as a defense-in-depth note: adding `extra="forbid"`
  to the request models would turn silent field-drop into an explicit 422 and harden
  against future field-name collisions.

---

## 3. Documented deferrals reproduced (`CONFIRMS-DOCUMENTED` — prove once, don't re-litigate)

Each was reproduced live to confirm it is real and current; all are already tracked.

| Case | What was proven live | Tracked in |
|---|---|---|
| TC-IA-004 | App role `oneai` is `rolsuper=t/rolbypassrls=t`; a **bogus all-zeros org GUC still returns hundreds of cross-org rows** → RLS policy never filters. The app-layer `org_id` filter is the *only* active control. | `FIX_BEFORE_PROD.md` "Enforce RLS" |
| TC-IA-035 | A **forged `company_admin` token** (dev secret, `org_id=B`) **reads *and* writes** org B's users (200/201). | "Rotate `JWT_SECRET`" + "Enforce RLS" |
| TC-IA-036 | A **member can forge** a same-`sub`/`org` token with `role=company_admin` and pass `require_company_admin` (real member token correctly 403s). | "Rotate `JWT_SECRET`" |
| TC-IA-034 | Cross-tenant **email-existence oracle**: B-admin's email → 409 vs fresh → 201 (AUD-04). | `FIX_BEFORE_PROD.md` AUD-04 |
| TC-IA-046 | A **demoted admin keeps `/users` authority** on the stale access token until expiry (≤15 min) — `require_company_admin` never re-checks the DB role. | "Access-token denylist" |
| TC-IA-003 | A **chunked / no-Content-Length body bypasses** `MaxBodySizeMiddleware` (which checks declared Content-Length only). | middleware docstring / proxy enforcement |
| TC-IA-005 | CORS: hostile origin gets **no** `Access-Control-Allow-Origin` (allowlist holds), but for allowed origins `allow_credentials=true` + wildcard methods/headers is broader than needed. | `FIX_BEFORE_PROD.md` "Lock CORS" |

> **Headline takeaway from TC-IA-004/035/036 together:** isolation today is a **single
> layer**. RLS is inert and the role/org claims are trusted straight from a JWT signed
> with a public dev secret, so secret compromise (or its mere presence in a non-prod-hardened
> deploy) = total cross-tenant compromise. This is exactly why "Rotate `JWT_SECRET`" and
> "Enforce RLS" are the two highest-priority items on `FIX_BEFORE_PROD.md`; this pass is
> the live proof of their blast radius.

---

## 4. Empirical verdicts on prior-audit claims (`CONFIRMS-FIXED` — no `REFUTES-FIX`)

Dynamic testing **confirmed every fix it could exercise**; nothing was refuted.

| Prior claim | Live verdict | Case(s) |
|---|---|---|
| **AUD-01** rotation single-use under concurrency | ✅ Holds — 50 trials × 10 concurrent refreshes: **exactly 1×200 + 9×401 every trial**, 0 double-mints. *Same-row pattern → corroborated, not independently proven: the black-box result is consistent with single-use and confirmed by code review of the conditional `UPDATE ... WHERE revoked_at IS NULL`; unlike 054/055 it carries no db-log contention artifact (the row lock serializes same-row races by design).* | TC-IA-053 |
| **AUD-05** create-user dup race → 409 not 500 | ✅ Holds — 50 trials all `201+409`, 0×500; db log shows **47 `users_email_key` UNIQUE violations** → the `IntegrityError→409` catch fired under real contention. | TC-IA-054 |
| **AUD-05** onboarding `IntegrityError` branches (audit's *untested* path) | ✅ Holds — concurrent same-slug → `201+409`, 1 org/slug; existing-admin-email → 409 with **0 orphan orgs** (whole tx rolls back). Closes the audit's "fixed-by-symmetry, not exercised" gap. | TC-IA-055 |
| **AUD-02** bcrypt >72-byte / multibyte password → 422 not 500 | ✅ Holds — 73-byte ASCII and 120-byte emoji both 422 at the `BcryptPassword` validator. | TC-IA-060/061 |
| **AUD-08** request-body size cap | ✅ Holds — 2 MiB declared Content-Length → 413 before the route. | TC-IA-002 |
| Constant-time login / no enumeration | ✅ Holds — wrong-password and unknown-email return **byte-for-byte identical** 401. | TC-IA-012 |
| `verify_password` swallows the bcrypt ValueError (login) | ✅ Holds — 200-byte password on `/auth/login` → 401, not 500. | TC-IA-014 |
| Token validation: `alg=none`, tampered sig, expiry, required `exp/aud/sub`, malformed claims | ✅ All hold — every variant → 401, malformed claims → 401 (not 500). | TC-IA-025/026/027/028/029 |
| Two-audience domain separation (company↔platform) | ✅ Holds both directions → 401 (platform-on-company correctly 401, not 403 — audience fails before the role gate). | TC-IA-022/023 |
| Missing bearer → 401 not 403 (`auto_error=False`) | ✅ Holds on all three protected surfaces. | TC-IA-024 |
| Soft-deleted / deactivated account: login, `/auth/me`, refresh all rejected | ✅ Holds — 401 on each; DB `is_active=f` confirmed. | TC-IA-013/044/045 |

---

## 5. What held (defenses that survived adversarial probing)

Cross-tenant PATCH/DELETE → 404 with the target unmutated (TC-IA-030/031); list returns
own-org-only (TC-IA-032); smuggled `org_id` ignored (TC-IA-033); role enum blocks
privilege escalation via the create body (`role=platform_admin` → 422, TC-IA-063); SQL/script
injection in `full_name`/`org_name` stored **literally** with no SQLi (parameterized ORM;
`users` table intact, TC-IA-065); error paths (404/405/422) leak no stack trace, JWT secret,
or DB DSN (TC-IA-006); refresh rotation single-use serially + logout idempotent + refresh-after-logout
rejected (TC-IA-040/042/043). These are the contract working as designed.

---

## 6. Coverage & limitations

- **Covered:** infrastructure (health, body cap, CORS, RLS posture, error hygiene),
  authentication (company + platform login, enumeration, inactive accounts, password
  byte-limit), authorization (role gate, audience split, full token-validation matrix),
  cross-tenant isolation (PATCH/DELETE/LIST/smuggle/forge), token lifecycle (rotation,
  logout, deactivation, demotion), and input validation on the auth surfaces. 46 cases.
- **Concurrency** cases ran 30–60 iterations with true `asyncio.gather` parallelism against
  the real server; the positive controls (TC-IA-053/054/055 holding while DYN-01 fires on
  the same pool) prove contention was genuinely exercised.
- **Not covered here (own targets next):** user-management business rules beyond authZ, the
  React frontend (the forged-name XSS-on-render question, auth-client refresh single-flight,
  routing), exhaustive fuzzing, and the connector/retrieval modules (not yet built). The
  **timing** constant-time check (TC-IA-016) is a noisy black-box measurement, not a rigorous
  proof. RLS enforcement under a *non-superuser* role was not exercised (it is inert today by
  design) — re-test when the dedicated DB role lands.
- **Operational note:** the persistent dev DB now holds several hundred run-stamped test
  orgs + deactivated users + revoked tokens from this pass. The demo org and its accounts
  were never mutated. A cleanup (truncate the identity tables and re-seed) is advisable
  before the next demo.

---

## 7. Prioritized actions from this pass

**Fix (new work this pass surfaced):**
1. **DYN-01** — make the last-admin guard atomic (`FOR UPDATE` / conditional `UPDATE` /
   SERIALIZABLE), 409 on the lost race. Add a concurrency test. *Add to `FIX_BEFORE_PROD.md`.*
2. **DYN-02** — canonicalize email (store + compare a normalized form); decide the
   uniqueness/case policy explicitly. *Fold into the pending AUD-04 decision.*
3. **DYN-03** — reject NUL/control chars at the validation boundary **and** add a global
   `DBAPIError` handler so unexpected DB errors map to 4xx, not 500.
4. **DYN-04** — add `extra="forbid"` to the identity request models (hardening).

**Already tracked — this pass is the live evidence, not new work:** Rotate `JWT_SECRET`,
Enforce RLS, access-token denylist, AUD-04 email oracle, Lock CORS, body-cap at the proxy
(TC-IA-003) — all confirmed real and current; see §3.

**Cross-reference:** Consistent with `2026-05-30_identity-module-deep-audit.md` and
`FIX_BEFORE_PROD.md`. Recommended additions to `FIX_BEFORE_PROD.md`: **DYN-01** (last-admin
concurrency), **DYN-02** (email canonicalization), **DYN-03** (NUL/`DBAPIError`→500).
Per-case evidence: [`testing/01_infrastructure-authn-authz/`](../../testing/01_infrastructure-authn-authz/).
