# Identity & Access Module — Deep Security + Bug Audit

**Date:** 2026-05-30
**Scope:** The standalone Identity & Access module — `backend/app/identity/` (auth, tenancy, platform admin, token lifecycle) and `frontend/src/identity/` (SPA auth client, providers, login) — plus the immediately-surrounding scaffold posture (config, Docker prod stages, nginx serve, CORS) where it bears on identity security.
**Method:** A seven-dimension multi-agent finder swept the module (AuthN/AuthZ & token lifecycle; tenant isolation & cross-tenant leakage; injection & input/output validation; crypto, secrets & sensitive-data exposure; concurrency/transactions & general correctness; frontend security & correctness; infrastructure/Docker/CI/config). Every candidate finding was then put through adversarial per-finding verification — reproduced from the exact code, cross-referenced against `docs/FIX_BEFORE_PROD.md` and the four recent fixes, and rated only if it survived as a real, *current* defect. This report records only verified findings, with exact `file:line` evidence. **No code was modified.** This is a static review; see Limitations.

> **⟳ Remediation status (updated 2026-05-31) — do NOT read the findings below as all-open.**
> **13 of the 15 NEW findings are FIXED and verified** (AUD-01, 02, 03, 05, 07, 08, 09, 10, 11, 12, 13, 14, 15) — proven by the expanded test suite (109 backend tests / 97.9% cov, frontend 97%) plus a live API smoke (overlong-password → 422, last-admin → 409, oversized-body → 413) and a prod-image browser check (CSP renders with zero violations).
> **2 are deferred *with documentation* in `docs/FIX_BEFORE_PROD.md`:** **AUD-04** (cross-tenant email oracle — pending the global-vs-per-org email-uniqueness decision) and **AUD-06** (refresh reuse-family revocation — needs an independent-commit path + the `audit_log` table).
> Caveats: **AUD-12**'s guard is fixed but **not unit-tested** (the mount race is timing-dependent); `onboard_organization`'s two `IntegrityError` branches (AUD-05) are fixed-by-symmetry with the tested `create_user` path but not independently exercised.

---

## 1. Executive summary

The four recently-landed fixes (constant-time login, config fail-closed, refresh domain-binding, `exp`/`aud`/`sub` required) **all hold in the current code** — see §5.

Beyond those, the audit confirmed **15 distinct NEW issues** (not on the already-fixed list and not in `FIX_BEFORE_PROD.md`) plus **1 already-tracked item** the sweep re-surfaced — **16 confirmed total**. Several finder agents independently filed the same two defects (the bcrypt >72-byte 500 was filed four times; the refresh-rotation race twice); those are consolidated into single findings below. AUD-13/14/15 are editorially grouped in §4 because each *sharpens* a tracked item, but all three are genuinely new (`already_known=false`) and are counted as NEW here.

### NEW issues by severity

| Severity | Count | IDs |
|---|---|---|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 3 | AUD-01, AUD-02, AUD-03 |
| Low | 8 | AUD-04, AUD-05, AUD-06, AUD-07, AUD-08, AUD-11, AUD-12, AUD-13 |
| Info | 4 | AUD-09, AUD-10, AUD-14, AUD-15 |

(15 NEW findings total, after consolidating the duplicate filings into single defects. The doc-drift companions are **AUD-09** (→ AUD-02, the bcrypt 72-byte 500) and **AUD-10** (→ the frontend auth client).)

### Headline risks (plain language)

1. **A refresh token is not truly single-use under concurrency (AUD-01, medium).** Two requests presenting the *same* refresh token at the same time can each mint a valid new token pair, because the revoke step issues a blind `UPDATE … WHERE id=…` with no `revoked_at IS NULL` guard. A stolen refresh token (returned in JSON and stored in `localStorage` per the documented deferral) can be raced against the legitimate client so both keep a live, independently-rotating session — and with no reuse-family revocation (AUD-06), nothing surfaces the anomaly.
2. **A valid-per-schema long password crashes account creation with a 500 (AUD-02, medium).** The pinned bcrypt 5.0.0 *raises* on inputs over 72 bytes (it no longer truncates), but `hash_password` has no guard and the request schemas allow up to 256 chars. A company-admin creating a user, or a platform-admin onboarding an org, with a long passphrase (common from password managers) gets an opaque HTTP 500 and no account — and the module docstring actively misdescribes the behavior (AUD-09).
3. **The production SPA is served with zero security response headers (AUD-03, medium).** `frontend/nginx.conf` sets only a `Cache-Control` header on `/assets/`. No CSP, no `X-Frame-Options`, no `nosniff`. Because the refresh token lives in `localStorage`, a single XSS = full token exfiltration; a restrictive CSP is the primary control that shrinks that blast radius, and its absence is undocumented.
4. **Cross-tenant email-existence oracle (AUD-04, low).** A company-admin can POST a user with an arbitrary email and read 409-vs-201 to learn whether that address already belongs to a user in *another* tenant — a cross-tenant existence disclosure that contradicts the module's own "never reveal existence in another org" invariant.

No critical or high-severity, immediately-exploitable-by-an-unauthenticated-attacker vulnerability was found. The medium findings are real and current; most lows are hardening or robustness gaps.

---

## 2. NEW findings (detailed, by severity)

### AUD-01 — Refresh-token rotation is not atomic: TOCTOU race mints two valid token pairs from one token
- **Severity:** Medium · **Category:** security / correctness · **CWE-362 / CWE-367 (TOCTOU race)**
- **Dimension:** AuthN/AuthZ logic & token lifecycle / Concurrency
- **Location:** `backend/app/identity/services/token_rotator.py:50-60` (`TokenRotator.consume`) → `backend/app/identity/repositories/refresh_token_repository.py:43-47` (`RefreshTokenRepository.revoke`)
- **Exploitable today:** Yes (under concurrency; both auth domains).

**Evidence.** `consume()` validates by reading the row and checking `stored.revoked_at is not None` *in Python* (`token_rotator.py:55`), then calls `self._refresh_tokens.revoke(stored)` (`:59`). `revoke()` does an in-memory `if refresh_token.revoked_at is None:` guard and a plain ORM flush (`refresh_token_repository.py:45-47`) — emitting `UPDATE refresh_tokens SET revoked_at=… WHERE id=:pk` with **no `WHERE revoked_at IS NULL`** at the SQL level. Each `/auth/refresh` request runs on its own session/transaction under default READ COMMITTED. Two concurrent requests presenting the same raw token both SELECT the row, both see `revoked_at IS NULL` (neither sees the other's uncommitted write), both pass the Python check, both call `issue_pair` (which INSERTs brand-new rows with *new* distinct hashes, so the `token_hash` UNIQUE index never collides), and both commit. The second blind `UPDATE` merely overwrites `revoked_at` — no conflict, no error.

The asymmetry is the proof: the logout path `revoke_by_hash` (`refresh_token_repository.py:55-60`) **does** carry `RefreshToken.revoked_at.is_(None)` in its `WHERE` and returns `rowcount` — it is race-safe. The rotation path is not. This violates the explicit "Rotation is single-use" invariant in `token_rotator.py:7-10`. Both `AuthService.refresh` (`auth_service.py:86`) and `PlatformAuthService.refresh` go through `consume()`.

**Impact.** The single-use invariant breaks under concurrency. Benign trigger: a multi-tab/retrying client fires the same token twice and gets two live sessions. Adversarial trigger: an attacker who stole a refresh token can race the legitimate client so both keep an independently-rotating chain; with no reuse-family revocation (AUD-06) and no anomaly surfaced, this is stealthy post-theft persistence. Not cross-tenant, not data-losing → medium.

**Remediation.** Make consumption atomic and conditional: revoke via `UPDATE … SET revoked_at=now() WHERE token_hash=:hash AND revoked_at IS NULL` (the pattern `revoke_by_hash` already uses) and treat `rowcount == 0` as `RefreshTokenInvalidError` (lost the race). `SELECT … FOR UPDATE` or a partial unique index would also close the window. Add a concurrency test asserting only one of two simultaneous refreshes succeeds.

---

### AUD-02 — Password >72 bytes raises an uncaught `ValueError` → HTTP 500 on user creation / org onboarding
- **Severity:** Medium · **Category:** correctness / input validation · **CWE-20 / CWE-248**
- **Dimension:** Crypto & sensitive-data / Injection & input validation
- **Location:** `backend/app/identity/security/password.py:24-33` (`hash_password`, `bcrypt.hashpw` at `:32`); reached via `UserService.create_user` (`user_service.py:61`) and `PlatformAuthService.onboard_organization` (`platform_auth_service.py:147`)
- **Exploitable today:** Yes (authenticated admin request; reliability/UX defect, not data exposure).

**Evidence.** The project pins **bcrypt 5.0.0** (`backend/uv.lock`; `pyproject.toml` only floors `>=4.2`). bcrypt 5.x **raises** `ValueError: password cannot be longer than 72 bytes` for any input ≥73 bytes — it no longer silently truncates (verified empirically against the installed library). `hash_password` calls `bcrypt.hashpw(raw_password.encode("utf-8"), salt)` with **no length guard and no try/except** (`password.py:31-33`). The request schemas admit such inputs: `UserCreateRequest.password = Field(min_length=8, max_length=256)` (`schemas/user_schemas.py:28`) and `OrganizationCreateRequest.admin_password = Field(min_length=8, max_length=256)` (`schemas/platform_schemas.py:37`). `max_length` counts **characters**; bcrypt's limit is **bytes**, so a multibyte password well under 72 chars can still exceed 72 UTF-8 bytes and trip the same raise.

The raised `ValueError` is not an `IdentityError`; `error_handlers.py:34-57` registers handlers only for `IdentityError` subclasses, and `main.py:47` registers no global/`ValueError` handler — so it bubbles to FastAPI's default handler → **HTTP 500**. Note the asymmetry: `verify_password` *does* wrap `checkpw` in `try/except ValueError: return False` (`password.py:45-48`), so login is insulated; only the create/onboard paths 500.

**Impact.** `POST /users` and `POST /platform/orgs` return an opaque 500 (not a clean 422) for a schema-valid long or multibyte password, and no account is created — indistinguishable from a real server fault. A self-inflicted correctness/availability defect on valid-per-contract input, common from password managers and passphrases. Both endpoints are authenticated, so not an unauthenticated DoS → medium.

**Remediation.** Enforce the real constraint at the validation boundary: a Pydantic `field_validator` on every password field rejecting `len(value.encode("utf-8")) > 72` → 422; **or** pre-hash (`base64(sha256(password))` then bcrypt) to lift the ceiling — applied uniformly in `hash_password` **and** `verify_password` so existing hashes stay verifiable. Do **not** bare-truncate `[:72]` (distinct long passwords would collide). Also fix the `password.py` docstring (AUD-09) and add a 73-byte test on both create paths.

---

### AUD-03 — Production nginx serves the SPA with no security response headers
- **Severity:** Medium · **Category:** security · **CWE-693 (missing protection mechanism); CWE-1021 clickjacking; XSS blast-radius)**
- **Dimension:** Infrastructure / Docker / config
- **Location:** `frontend/nginx.conf` server block (lines 3-18); the only `add_header` is `Cache-Control` inside `location /assets/` (lines 10-13). Served by the prod stage in `frontend/Dockerfile` (line 33 copies `nginx.conf`; line 34 copies the built SPA).
- **Exploitable today:** Defense-in-depth gap in the prod serve path; amplifies the documented `localStorage` refresh-token exposure.

**Evidence.** The prod image (`nginxinc/nginx-unprivileged`) serves the built SPA via `frontend/nginx.conf`, which adds exactly one header (`Cache-Control`, and only for `/assets/`). The HTML shell and every response ship with **no** `Content-Security-Policy`, `X-Frame-Options`/`frame-ancestors`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, or `Permissions-Policy`. A repo-wide search for these header names returned zero matches, and there is no separate reverse-proxy/ingress in the compose stack that would add them. The amplifier is real: the refresh token is persisted in `localStorage` (`frontend/src/identity/authClient.ts:51`, key `oneai.refresh_token`), so any injected script can exfiltrate it = account takeover.

This is **not** covered by `FIX_BEFORE_PROD.md`: the "Enforce TLS 1.3" item (line 84) covers transport + the `Secure` cookie, and the "Lock CORS" item (line 85) covers the *API's* cross-origin policy — neither addresses static-serve response security headers.

**Impact.** Missing CSP removes the primary mitigation that would shrink the XSS→token-theft blast radius; missing `X-Frame-Options`/`frame-ancestors` makes the app clickjackable. A prod-serve hardening gap, not a localhost-only concern.

**Remediation.** Add headers in the nginx **server block** (not only inside `location /assets/`, or they will not apply to the `index.html` shell), using `add_header … always` so they attach to error responses too: `X-Frame-Options: DENY` (or CSP `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, and a `Content-Security-Policy` scoped to `self` + the API origin. Add HSTS at the TLS-terminating ingress.

---

### AUD-04 — Cross-tenant email-existence oracle via unscoped `email_exists` → deterministic 409
- **Severity:** Low · **Category:** security · **CWE-204 (observable response discrepancy / cross-tenant enumeration)**
- **Dimension:** Tenant isolation / Injection & input validation
- **Location:** `backend/app/identity/repositories/user_repository.py:75-78` (`email_exists`); consumed by `UserService.create_user` (`user_service.py:54-55`) and `PlatformAuthService.onboard_organization` (`platform_auth_service.py:136`)
- **Exploitable today:** Yes (authenticated company-admin; reveals only boolean existence).

**Evidence.** `email_exists` runs `select(User.id).where(User.email == email)` with **no org filter** — a deliberate global uniqueness check (`email` is `unique=True` globally, `user.py:36`). `create_user` calls it and raises `DuplicateUserError` → 409 (`error_handlers.py:42`) when the email exists in **any** org; otherwise the insert proceeds → 201. `POST /users` is gated by `require_company_admin`, so a company-admin of org A can submit `ceo@competitor.example` and distinguish 409 (the email belongs to a user in some org) from 201. This contradicts the module's own "never reveal existence in another org" invariant (`get_in_org` → 404). On the platform-onboarding path the caller is the trusted platform admin, so the cross-tenant bite lands only on the company-admin `/users` surface.

Two self-limiting nuances (which keep this at low): a *negative* probe doesn't just return 201 — it **creates a real user** in the attacker's own org, and `deactivate_user` is a soft delete (`user_service.py:92-102`), so the row and its globally-unique email persist; re-probing a burned address then returns 409 from the attacker's own row, polluting the signal and leaving an obvious audit trail of created-then-deactivated junk users. The oracle is clean on the first probe of any address.

**Impact.** A company-admin can confirm whether specific email addresses are registered as users of other tenants — membership/existence disclosure across the tenant boundary. No content leaks; only existence. For a GDPR/DACH product this is a genuine, if low, cross-tenant information disclosure.

**Remediation.** Decide explicitly whether global email uniqueness is a product requirement. If yes, document this oracle and rate-limit create attempts; if no, scope the duplicate check per-org (`email_exists_in_org(email, org_id)`) with `UNIQUE (org_id, email)` so a 409 only ever reveals a collision within the caller's own tenant. **At minimum, add this oracle to `FIX_BEFORE_PROD.md`** so it is a tracked, conscious deferral rather than an undocumented gap.

---

### AUD-05 — Check-then-insert on email/slug returns HTTP 500 instead of 409 under concurrency (no `IntegrityError` handler)
- **Severity:** Low · **Category:** correctness · **CWE-362 (TOCTOU)**
- **Dimension:** Concurrency / transactions
- **Location:** `UserService.create_user` (`user_service.py:54-64`) and `PlatformAuthService.onboard_organization` (`platform_auth_service.py:134-150`)
- **Exploitable today:** Yes (under a genuine concurrent race; the serial duplicate path is correctly handled).

**Evidence.** Both create paths are check-then-act: `email_exists` / `get_by_slug` (a SELECT) then `add()` (an INSERT that flushes — `user_repository.py:80-84`). The DB UNIQUE constraints exist (`organizations.slug` and `users.email`, migration `0002` lines 64/109; ORM `unique=True` at `organization.py:27`, `user.py:36`). Two concurrent requests with the same email/slug both pass the pre-check; the second INSERT raises SQLAlchemy `IntegrityError`. `IntegrityError` is not an `IdentityError`, so `error_handlers.py` does not map it and `main.py` registers no global handler → it propagates as an unhandled **HTTP 500**, breaking the documented 409 `DuplicateUserError`/`DuplicateOrganizationError` contract. The session dependency rolls back (`database.py:44-51`), so there is no partial commit — only the wrong status code. `IntegrityError` appears nowhere in `backend/app/`.

**Impact.** Two near-simultaneous identical-email/slug admin requests yield one success and one 500 instead of one 409. No data corruption (the constraint still prevents the duplicate row); flaky, hard-to-debug behavior under real concurrent admin activity.

**Remediation.** Catch `IntegrityError` on the add path and translate `users.email` / `organizations.slug` violations to `DuplicateUserError` / `DuplicateOrganizationError` (409), keeping the pre-check as the fast path; or register a global `IntegrityError` handler mapping constraint names to 409. Add a forced-`IntegrityError` test asserting 409.

---

### AUD-06 — Refresh-token reuse is detected but does not revoke the descendant chain (no reuse-family revocation)
- **Severity:** Low · **Category:** quality / hardening · **CWE-613**
- **Dimension:** AuthN/AuthZ & token lifecycle
- **Location:** `backend/app/identity/services/token_rotator.py:34-60`
- **Exploitable today:** No (hardening gap; single-use itself is enforced).

**Evidence.** `consume()` lumps every failure mode (unknown, wrong subject_type, revoked, expired) into one generic `RefreshTokenInvalidError` (`token_rotator.py:52-58`). Single-use **is** enforced (a revoked token re-presented → 401; proven by `test_refresh_reusing_old_token_raises_invalid`). But on a detected reuse it does **not** revoke the currently-active descendant token(s) for that subject. There is no `revoke_all`/`revoke_for_subject`/`revoke_family` method anywhere in the repository (`refresh_token_repository.py` exposes only `get_by_hash`, `add`, `revoke(row)`, `revoke_by_hash`). RFC 6819 / OAuth BCP says reuse implies a leak → kill the family.

**Impact.** If a refresh token leaks and the attacker uses it, the attacker's rotated chain stays valid; the victim's later reuse only fails the victim's own request, it does not eject the attacker. Compounds AUD-01. Hardening gap, not immediately exploitable.

**Remediation.** On a reuse hit (token found but `revoked_at IS NOT NULL`), revoke all unrevoked refresh tokens for that `subject_id + subject_type` and emit a security event once `audit_log` exists.

---

### AUD-07 — `company_admin` can deactivate/demote the org's last admin (and self) — no last-admin guard
- **Severity:** Low · **Category:** correctness / availability · **CWE-841 (improper enforcement of behavioral workflow)**
- **Dimension:** Tenant-scoped workflow (within-tenant; not an isolation breach)
- **Location:** `UserService.update_user` (`user_service.py:67-90`) and `deactivate_user` (`user_service.py:92-102`)
- **Exploitable today:** Yes, as a self-inflicted/mistaken foot-gun (within a single org).

**Evidence.** `update_user` applies role/`is_active` changes to any same-org user with no check that the change removes the org's last active `company_admin`, and no guard against an admin acting on their own row (`user_service.py:84-87`). `deactivate_user` likewise sets `is_active=False` with no last-admin/self-target check (`:101`). No admin-counting query exists (`user_repository.py` exposes no count of active admins); no `LastAdminError` exists. Because there is no invite flow (`FIX_BEFORE_PROD.md:51`) and members are blocked from `/users` by `require_company_admin` (→ 403), an org that loses its last active admin has **no in-app recovery path** — and a self-deactivated sole admin cannot even log back in (constant-time login rejects inactive accounts). The existing test `test_update_user_same_org_applies_changes` actually codifies the unguarded behavior. The blast radius is a single org (no cross-tenant access), so this is an availability/lockout defect, not an isolation breach.

**Impact.** An org can be locked out of its own user-management surface by one mistaken or careless admin action (self-demotion, self-deactivation, or demoting the last peer admin), recoverable only via platform-admin or direct DB intervention — an operational/SLA pain point for mid-market customers.

**Remediation.** Before committing a change that would remove the last active `company_admin` (role change away from admin, or `is_active=False` on an admin), count remaining active admins and raise `LastAdminError` → 409 if it would drop to zero. Optionally forbid an admin from deactivating their own account in the same call. Add a (cross-org-safe) last-admin-guard test.

---

### AUD-08 — No request-body size cap; `refresh_token` field is unbounded (`min_length` only)
- **Severity:** Low · **Category:** security / DoS-surface · **CWE-770 (resource allocation without limits)**
- **Dimension:** Injection & input/output validation
- **Location:** `backend/app/identity/schemas/auth_schemas.py:31` (`RefreshRequest.refresh_token`) and `:37` (`LogoutRequest.refresh_token`) — `Field(min_length=1)` with no `max_length`; no app-level body-size middleware
- **Exploitable today:** Yes (minor DoS surface only; no data exposure).

**Evidence.** Both `refresh_token` fields declare `Field(min_length=1)` with **no** `max_length`, inconsistent with the surrounding schemas that bound their strings (`password` is `Field(min_length=1, max_length=256)`; org name/slug/admin fields are all bounded in `platform_schemas.py:33-37`). `main.py` registers only `CORSMiddleware` — no Content-Length / body-size middleware, and no reverse proxy in the ASGI layer — so Starlette buffers the full body and Pydantic validates before rejection. Impact is bounded: the token is sha256'd to a fixed 64 chars and matched, so an oversized value can never match a real row; cost is wasted buffering + a single fixed-cost hash (not a bcrypt amplification path).

**Impact.** Unbounded request bodies are buffered and processed before rejection — a minor DoS surface, cheap to mitigate, no data exposure.

**Remediation.** Add `max_length` (e.g. 512, comfortably above the ~64-char issued token) to the `refresh_token` fields, and — more importantly — enforce a global maximum request-body size at the ASGI/proxy layer (Starlette middleware or the reverse proxy's `client_max_body_size`) so all endpoints reject oversized payloads uniformly.

---

### AUD-09 — `password.py` docstring misdescribes bcrypt 72-byte handling, hiding the real failure mode
- **Severity:** Info · **Category:** quality (docs-as-runtime-input, rule A4)
- **Location:** `backend/app/identity/security/password.py:13` ("bcrypt's 72-byte input limit is acceptable for the MVP") and `:41-43` ("a malformed/empty stored hash raises ValueError inside bcrypt")
- **Exploitable today:** No runtime impact; it is the proximate cause of AUD-02.

**Evidence.** The docstrings describe bcrypt 4.x semantics (the limit merely "acceptable"; `ValueError` only from a bad stored hash). Under the pinned bcrypt 5.0.0, an **input** >72 bytes is itself a `ValueError` trigger in both `hashpw` and `checkpw`. `verify_password` correctly swallows it (`return False`, symmetric for known vs unknown accounts — not an enumeration oracle), but the docstring's narrow framing is why the unguarded `hash_password` path (AUD-02) was overlooked.

**Remediation.** Update both docstrings: bcrypt 5.x **raises** `ValueError` on inputs >72 bytes (no truncation); `hash_password` must guard/validate length; `verify_password` intentionally returns `False` on that `ValueError`. Tie the description to the pinned major version.

---

### AUD-10 — `authorizedFetch` docstring describes an `alreadyRetried` guard that does not exist
- **Severity:** Info · **Category:** quality (docs/reality drift on a security-relevant control)
- **Location:** `frontend/src/identity/authClient.ts:161` ("the replay sets `alreadyRetried`") vs the implementation at `:166-175`
- **Exploitable today:** No; risk is a future maintainer mis-edit.

**Evidence.** The docstring attributes the no-infinite-loop guarantee to a replay setting an `alreadyRetried` flag. No such flag exists — a repo-wide grep matches only the comment. The actual (correct) guard is structural: the replay at `:174` calls `sendWithBearer()` directly (a plain fetch at `:177-188`), **not** `authorizedFetch()`, so a second 401 surfaces to the caller and cannot recurse. The behavior is sound; the documented mechanism is fictional, and a maintainer trusting it could route the replay back through `authorizedFetch` and create the very loop the comment claims to prevent.

**Remediation.** Rewrite the comment to describe the real structural guard: the replay calls `sendWithBearer` directly (not `authorizedFetch`), so a second 401 surfaces without another refresh attempt.

---

### AUD-11 — Concurrent `authorizedFetch` calls can destroy a valid session via the single-use rotation race (currently latent / dev-reachable)
- **Severity:** Low (rated low — latent in prod builds) · **Category:** correctness · **CWE-362**
- **Location:** `frontend/src/identity/authClient.ts:166-175` (`authorizedFetch`) + `:102-119` (`refreshTokens`)
- **Exploitable today:** Latent in production builds; **reachable in dev** via React 19 StrictMode double-invocation.

**Evidence.** `authorizedFetch` handles each 401 independently with no in-flight-refresh dedup/mutex/promise-sharing; `refreshTokens()` reads the single stored refresh token, POSTs it, and on any non-ok response calls `setTokens(null)` (`:113`), wiping the session. Backend rotation is single-use (AUD-01 mechanics). If two `authorizedFetch` calls race a 401, both read the same stored token; the first rotation revokes it server-side, the second presents the now-revoked token → 401 → `setTokens(null)` → spurious full logout. Today the only consumer is `fetchCurrentUser()` at bootstrap, so prod is latent — **but** `main.tsx:22` wraps `AuthProvider` in `<StrictMode>`, and React 19 retains StrictMode's dev double-invocation of effects; the bootstrap effect's `cancelled` flag only gates the `setState`, not the in-flight fetch, so a hard refresh with a stored token fires two `/auth/me` → two refreshes → the second wipes the localStorage token *in dev today*. The next authenticated feature that fires parallel requests makes it real in prod too.

**Impact.** Spurious full logout (and an extra revoked token row) whenever two authenticated requests race a 401. Reliability/UX defect, not a data leak.

**Remediation.** Serialize refresh: a module-level `refreshInFlight: Promise<string> | null` — the first 401 creates it, concurrent callers `await` the same promise instead of each POSTing `/auth/refresh`; clear it in `finally`.

---

### AUD-12 — Bootstrap `/auth/me` can overwrite a fresh login (mount-race, no cancel-on-login)
- **Severity:** Low · **Category:** correctness · **CWE-362**
- **Location:** `frontend/src/identity/AuthProvider.tsx:52-75` (bootstrap effect) vs `:77-87` (`login`/`platformLogin`)
- **Exploitable today:** Yes, in a narrow window (stored token from a prior session + login submitted while bootstrap is in flight).

**Evidence.** The mount-time bootstrap effect, when a refresh token is already stored, calls `fetchCurrentUser()` and on resolution unconditionally runs `setUser(currentUser)+setStatus("authenticated")` (`:62-64`); its only guard is `cancelled`, set true **only on unmount** (`:72-74`). `/login` renders unconditionally (`App.tsx:36-43`, not behind `ProtectedRoute`) and the form submits during `status==="loading"` (`LoginPage.tsx:32-36`). `AuthProvider` sits above the Router, so it does not unmount on `/login → /` — `cancelled` stays false. If the user submits new credentials while the bootstrap `/auth/me` is in flight, `login()` sets the new identity, then the in-flight bootstrap resolves and overwrites it with the **old** stored session. Worse, tracing `authClient.ts`: the bootstrap's in-flight `/auth/refresh` holds the old refresh token read synchronously at `:103`, so on completion it can `setTokens(old-pair)` (`:117`), clobbering the new user's in-memory access token — i.e. actual API authority, not just the displayed name, can revert to the prior user. On a shared device this surfaces the prior user's identity to the new one. The window is narrow (the bootstrap is 2-3 sequential round trips on mount; normally it resolves and `LoginPage` navigates away before submit), but autofill or the `DevCredentialsPanel` click-to-fill compresses type-time and widens it.

**Impact.** Wrong-identity display and, in the bad interleaving, reversion to the prior user's authenticated session — confusing, and a shared-device exposure of the previous user's name/org. Severity stays low because the trigger window is narrow.

**Remediation.** Cancel the bootstrap when an explicit auth action wins: a "superseded" ref/flag that `login`/`platformLogin` set and the bootstrap `.then` checks (in addition to `cancelled`); and/or gate the login form behind `status !== "loading"`.

---

## 3. Already-tracked items the audit re-surfaced (`already_known=true`)

- **Frontend prod build silently defaults `VITE_API_URL` to `http://localhost:8000` if the build-arg is omitted** (`frontend/Dockerfile:26-27`). An omitted `--build-arg VITE_API_URL=…` compiles a SPA whose API base is hard-wired to localhost — a deployable-but-broken prod artifact, no security impact. This is already recorded in the companion scaffold audit (`docs/audits/2026-05-30_scaffold-setup-and-security-audit.md`, finding #3 and the "before first frontend deploy" pre-deploy gap), and the README documents passing the arg. **Note on the obvious fix:** simply dropping the Dockerfile ARG default would *not* surface the misconfiguration loudly — the TS itself carries `import.meta.env.VITE_API_URL ?? "http://localhost:8000"` (`authClient.ts:16`, `HomePage.tsx:15`), so the app-code fallback would silently take over. Only an explicit build-time assertion (reject a localhost origin when building for prod) or removing the TS fallback too would actually fail the build. Tracked, not new work.

---

## 4. NEW findings that sharpen tracked items (AUD-13/14/15)

These are **new** findings (`already_known=false` — none is on the deferral list), grouped here editorially because each tightens an item already in `FIX_BEFORE_PROD.md` with a concrete missing fix. They are counted in the NEW totals in §1.

- **AUD-13 (low, security) — Demo seed script is baked into the prod backend image (not merely runnable).** `backend/.dockerignore` excludes `tests/` but **not** `scripts/`, so the prod stage's `COPY . .` (`backend/Dockerfile:39`) bakes `scripts/seed_identity.py` — with the verbatim demo platform-admin/company-admin/member passwords — into the shipped image layers, extractable via `docker save`/layer inspection. The runtime guards **do** hold (prod `CMD` is plain `uvicorn`, and `_refuse_in_production()` hard-exits when `APP_ENV=production`, which the prod Dockerfile sets), so the seed cannot self-run; this is image-content hygiene, not an exploitable start path. The passwords are already public in git/docs, so exposure is not widened. *Fix:* add `scripts/` (or at least `scripts/seed_identity.py`) to `backend/.dockerignore`, and complete the tracked seed removal (`FIX_BEFORE_PROD.md` "Demo credentials" section). *Sharpens the tracked seed-deletion item with a concrete `.dockerignore` change it does not mention.*
- **AUD-14 (info, security) — Platform-admin identity (incl. role) is synthesized client-side from the typed email, not a verified server response.** `synthesizePlatformAdmin` (`AuthProvider.tsx:27-37`, called from `platformLogin` `:83-87`) builds `role='platform_admin'` from the email's local-part; `/platform/login` returns tokens only and there is no `/platform/me`. **Safe today** — a 200 requires server-verified platform credentials, and `user.role` is used only as a display label (`HomePage` `ROLE_LABELS`); `ProtectedRoute` gates on auth `status`, never on `role`. It becomes a real client-side access-control weakness the moment any UI is gated on this synthesized role. *Aligns with the tracked `GET /platform/me` rehydrate item (`FIX_BEFORE_PROD.md:69`) — extend it to also be the role source, and document that this identity is display-only and must never back an authorization decision.*
- **AUD-15 (info, ops) — Prod images define no `HEALTHCHECK`.** Healthchecks exist in `docker-compose.yml` for the dev stack but neither prod stage bakes a `HEALTHCHECK` (`backend/Dockerfile:36-44`, `frontend/Dockerfile:31-35`). `/health` already does a real DB round-trip (`api/routes/health.py:23`), so a backend healthcheck is trivial. Operational only; most k8s/ECS deployments define probes externally. *Not in `FIX_BEFORE_PROD.md`; informational.*

---

## 5. Verification of the four recent fixes

All four still hold in the current code (absence of any contradicting finding + direct re-read):

| Fix | Holds? | Evidence |
|---|---|---|
| **1. Constant-time login** | ✅ Holds | `auth_service.py:61-67` resolves the user, then **always** runs `verify_password` against the real hash or `DUMMY_PASSWORD_HASH` (`security/password.py:54`) before the combined `user is None or not is_active or not password_ok` check. Unknown/inactive accounts pay the same bcrypt cost. Same pattern in `platform_auth_service.login`. |
| **2. Config fail-closed in prod** | ✅ Holds | `core/config.py:84-107` `_forbid_insecure_defaults_in_production` raises `InsecureConfigurationError` (a hard boot failure) if `JWT_SECRET` or `POSTGRES_PASSWORD` still equals its dev default when `app_env == 'production'`. |
| **3. Refresh rotation domain-bound** | ✅ Holds | `token_rotator.py:54` rejects a token whose `subject_type != expected_subject_type` (the calling domain — `'user'` vs `'platform_admin'`) **without** revoking it, so a foreign token can't be used to DoS another domain's session. |
| **4. `decode_access_token` requires `exp`/`aud`/`sub`** | ✅ Holds | `security/tokens.py:82` passes `options={"require": ["exp", "aud", "sub"]}` to `jwt.decode`, with `audience=audience` enforced and `ExpiredSignatureError` ordered before `InvalidTokenError` (`:84-88`). A token missing `exp` is rejected, not treated as non-expiring. |

Note: AUD-01 (rotation race) is **adjacent to but distinct from** fix #3 — fix #3 binds the *domain*; AUD-01 is the *atomicity* of the revoke. The two do not conflict.

---

## 6. Coverage & limitations

**Dimensions reviewed (all seven):**
1. AuthN/AuthZ logic & token lifecycle — login, refresh rotation, logout, JWT decode, domain separation. (AUD-01, AUD-06; fixes #1/#3/#4)
2. Tenant isolation & cross-tenant data leakage — org-scoped queries, `email_exists`, platform vs company sessions. (AUD-04, AUD-07)
3. Injection & input/output validation — Pydantic bounds, body size, error mapping. (AUD-02, AUD-08, AUD-05)
4. Cryptography, secrets & sensitive-data exposure — bcrypt usage, password hashing, dummy-hash timing, seed credentials in images. (AUD-02, AUD-09, AUD-13)
5. Concurrency, transactions & general correctness — TOCTOU on rotation and on create-paths. (AUD-01, AUD-05)
6. Frontend security & correctness — auth client, refresh serialization, mount races, synthesized identity. (AUD-10, AUD-11, AUD-12, AUD-14)
7. Infrastructure, Docker, CI, dependencies & config — nginx headers, prod Dockerfile stages, `.dockerignore`, healthchecks, `VITE_API_URL`. (AUD-03, AUD-13, AUD-15)

**Limitations (honest).** This was a **static code review** only. No dynamic testing was executed: no DAST, no fuzzing of the auth endpoints, no concurrency load test to *observe* the AUD-01/AUD-05/AUD-11 races firing under real contention (they are reproduced by code-tracing + the documented transaction model, not by a running load harness). No dependency-CVE / SCA scan was run against the locked dependency tree (the bcrypt 5.0.0 behavior in AUD-02 was confirmed empirically against the installed library, but the broader lockfile was not CVE-scanned). Runtime/load behavior, RLS enforcement under a non-superuser role, and the production TLS/ingress posture were not exercised. Findings are grounded in exact `file:line` reads; the severity ratings assume the documented deferrals (refresh token in `localStorage`, no rate-limiting, RLS inert) remain in place.

---

## 7. Prioritized remediation plan

**Fix now (before the next merge that touches identity):**
1. **AUD-02** — guard password length (422 on `>72` UTF-8 bytes) or pre-hash; fix the `password.py` docstring (AUD-09). Cheap, removes a 500 on a valid-input core flow.
2. **AUD-01** — make `consume()`'s revoke atomic and conditional (`UPDATE … WHERE token_hash=:hash AND revoked_at IS NULL`, treat `rowcount==0` as invalid). Restores the single-use invariant the module advertises.
3. **AUD-03** — add security response headers to the nginx **server block** (`always`): CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`. Cheap, shrinks the documented `localStorage` XSS blast radius.

**Fix soon (this milestone):**
4. **AUD-05** — map `IntegrityError` on `users.email` / `organizations.slug` to 409.
5. **AUD-11** — serialize refresh in `authorizedFetch` (`refreshInFlight` promise). Also resolves the dev StrictMode logout.
6. **AUD-12** — cancel-on-login in `AuthProvider`; gate the login form on `status !== "loading"`.
7. **AUD-07** — add a last-admin guard (`LastAdminError` → 409) and forbid self-deactivation.
8. **AUD-06** — reuse-family revocation on detected refresh reuse (compounds AUD-01).
9. **AUD-08** — bound the `refresh_token` fields + add a global body-size cap.
10. **AUD-13** — add `scripts/` to `backend/.dockerignore` (do alongside the tracked seed removal).
11. **AUD-10**, **AUD-09** — docstring corrections (security-relevant doc drift).

**Accepted / to-document deferrals (track, don't fix blindly):**
- **AUD-04** — if global email uniqueness stays, **add the cross-tenant existence oracle to `FIX_BEFORE_PROD.md`** as a conscious deferral (and rate-limit creates); otherwise scope email uniqueness per-org.
- **AUD-14** — fold into the tracked `GET /platform/me` item; document that the synthesized role is display-only.
- **AUD-15** — add a prod `HEALTHCHECK` or document that probes are defined at the orchestration layer.
- **`VITE_API_URL` localhost default** — already tracked in the scaffold audit; note that removing only the Dockerfile default does not fix it (the TS fallback persists).

**Cross-reference:** This report is consistent with `docs/FIX_BEFORE_PROD.md`. The items above that touch tracked entries (seed removal, `GET /platform/me`, RLS, CORS, TLS) sharpen — and do not contradict — that checklist. AUD-04 and AUD-15 are the two findings recommended for *addition* to `FIX_BEFORE_PROD.md`.
