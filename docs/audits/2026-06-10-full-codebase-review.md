# One AI MVP — Full-Codebase Audit Report

> **Status as of 2026-09-06:** historical record — the findings and the §5 addendum below are as-of
> 2026-06-10 and are left exactly as written. Three status facts have since moved on.
> (1) The §5 backlog line **"refresh-token localStorage"** is **closed**: the company refresh token was
> moved to an httpOnly cookie in code on 2026-06-14 (per the `docs/FIX_BEFORE_PROD.md` httpOnly-cookie
> row) and committed on 2026-06-15 as **`7e90add`** — which rewrites `frontend/src/identity/authClient.ts`
> and adds `backend/app/identity/routes/cookies.py`.
> (2) The §5 fix pass, described there as an "uncommitted working tree", was committed on 2026-06-11 as
> **`4808ea5`** (present on `main` and `ask-tools-loop`).
> (3) §5's "migration chain at `0013` head" is an as-of-2026-06-10 snapshot: the live dev database is
> stamped at **`0022_counterparty_summary_v3`** — measured 2026-09-06
> (`docs/audits/2026-09-06_built-vs-docs-map.md` §3) — and that revision's file exists only in the
> uncommitted working tree; the newest migration whose content is in git is `0021_counterparty_summary_v2`.

**Date:** 2026-06-10
**Scope:** ~31k lines reviewed — backend app (~12k), frontend (~12k), tests (~8.4k), plus DB migrations and ops scripts. FastAPI (async SQLAlchemy 2.0) + React 19/Vite/Tailwind v4 + Postgres 16/pgvector, dockerized; multi-tenant.
**Method:** 16 finder agents (domain-partitioned) → per-claim adversarial verification against source. Findings below are the survivors: every entry is verdict **CONFIRMED** (REFUTED claims removed). Counts: **0 critical, 8 high, 40 medium, 47 low.**

---

## 1. Executive summary

The codebase is in solid, deliberately-engineered shape for an MVP: tenant isolation is enforced at four layers (org_id NOT NULL, FORCE RLS policies, app-level scoping, least-privilege runtime roles), the sync/ingest design has a genuine never-lose-mail cursor model, and the team tracks its own pre-production debt in `docs/FIX_BEFORE_PROD.md`. There are **zero critical findings** and no live cross-tenant data leak reachable through the HTTP surface. The dominant themes are: (a) **GDPR-erasure correctness** — the org-erase endpoint emits a deletion certificate while leaving every Connect/entity PII table intact, and has no cross-tenant containment test; (b) **transport/credential security on the IMAP path** — the credential-verification connection disables TLS certificate validation and the non-SSL path never attempts STARTTLS; (c) **event-loop blocking** — synchronous bcrypt and synchronous email parsing on async paths serialize all tenants on an instance under modest load; (d) **frontend credential hand-off fragility** — closing a create/onboard/connect drawer mid-submit silently destroys the one-time password and leaves a ghost success panel; and (e) a recurring **test-coverage gap** where the project's own "cross-tenant negative test per tenant-scoped method" rule is unmet for several repositories and the most destructive endpoint (erasure). The must-fix set before any non-local deployment: wire connector/entity erasure into `ErasureService` (HIGH), pass an `ssl.create_default_context()` on the IMAP verify path (HIGH, one line), chunk the unbounded `UID FETCH` that bricks large first syncs (HIGH), offload bcrypt and `parse_email` off the event loop (HIGH/MEDIUM), gate `DevCredentialsPanel` behind `import.meta.env.DEV` (HIGH), and fix the mid-submit drawer-close credential loss (HIGH). The dev-only `ingest_imap_dump.py` writing on the BYPASSRLS engine (HIGH) and the missing erasure cross-tenant test (HIGH) round out the high tier. None of the high findings is exploitable by an anonymous external attacker today; most are gated by platform-admin/company-admin auth, dev-only tooling, or require an active MITM — but they violate the project's own hardest rules and stated invariants, and several emit artifacts (deletion certificates, "succeeded" run ledgers, "Copied" toasts) that actively assert the opposite of what happened.

---

## 2. Findings by severity

### CRITICAL

None.

---

### HIGH (8)

**`backend/app/identity/services/erasure_service.py:115` — GDPR org erasure leaves all Connect/entity PII intact while emitting a deletion certificate.**
The erase path touches only refresh_tokens, support-grant decider emails, users, and org status; every Connect/entity tenant table (connector_connection with the encrypted IMAP credential, email_message/email_recipient/email_attachment, the whole person/company graph) survives, yet `ErasureCertificateResponse` asserts erasure and status is set to `offboarded`.
*Failure:* `POST /platform/orgs/{id}/erase` on an org with an ingested mailbox commits, returns a signed-looking certificate, and leaves full email bodies + a decryptable IMAP credential queryable — Art. 17 breach. Tracked as FIX_BEFORE_PROD CA-CONN-01/03 (erasure-hook registry) but live today.
*Evidence:* `tokens_deleted = ...; emails_scrubbed = ...; users_erased = await self._users.delete_all_in_org(org_id)` — no connectors/entities repo is even wired into the constructor.
*Verdict:* CONFIRMED.

**`backend/app/connectors/imap/client.py:69` — IMAP `verify_login` disables TLS certificate verification (credential-disclosure on the path that always carries the password).**
`IMAP4_SSL` is constructed with no `ssl_context`, so Python defaults to an unverified context (`check_hostname=False`, `CERT_NONE`). The fetch path (`fetch_session.py:238`) correctly passes `ssl.create_default_context()` — only the credential-verify path is unprotected.
*Failure:* An on-path attacker presents any self-signed cert during the test-connection flow; the handshake succeeds and `client.login()` ships the org's IMAP username+password to the attacker.
*Evidence:* `imaplib.IMAP4_SSL(params.host, params.port, timeout=self._timeout)`
*Verdict:* CONFIRMED. One-line fix.

**`backend/app/connectors/imap/fetch_session.py:187` — unbounded `UID FETCH` command bricks any large first sync.**
`_blocking_sizes` is called with ALL pending UIDs of a folder, comma-joined into one command line that exceeds server limits (e.g. Dovecot's 64KB `imap_max_line_length`). The server replies BAD, imaplib raises `IMAP4.error` (not the handled `typ!='OK'` path), the run fails, and the cursor never advances — every retry rebuilds the identical command, so the mailbox is permanently unsyncable.
*Failure:* Initial sync of a ~10k+ message mailbox (the normal first-sync case) deterministically fails on common self-hosted servers.
*Evidence:* `typ, data = self._client.uid("FETCH", ",".join(map(str, uids)), "(UID RFC822.SIZE)")`
*Verdict:* CONFIRMED. Needs chunking / sequence-set range compression.

**`backend/app/identity/services/auth_service.py:75` — synchronous bcrypt (rounds=12, ~300ms) on the async login path blocks the entire event loop.**
`verify_password` runs inline in `async def login` with no executor offload; same pattern at `platform_auth_service.py:89/178`, `user_service.py:80`, `erasure_service.py:108`.
*Failure:* Any login (incl. unknown-email attempts that deliberately bcrypt a dummy hash) freezes all in-flight requests for every tenant on the instance for ~300ms. The repo's own live test (FIX_BEFORE_PROD N-01) measured 40 concurrent invalid logins → ~10.7s median latency. Rate-limiting (the only tracked remediation) does not fix legitimate concurrent logins.
*Evidence:* `password_ok = verify_password(password, password_hash)`
*Verdict:* CONFIRMED. Fix: `asyncio.to_thread`.

**`backend/scripts/ingest_imap_dump.py:82` — disk-ingest driver writes tenant-scoped rows on the BYPASSRLS `oneai_global` engine, skipping the RLS seam for an entire ingest path.**
Uses `GlobalSessionLocal()` directly instead of `scoped_session(org_id)`. The production analog (`connector_sync_runner.py:163`) and sibling script (`seed_identity.py:166`) both use `scoped_session`.
*Failure:* `core/database.py`'s own invariant names the hazard — "a tenant flow wrongly on the global engine fails open/silent." Any missed org_id filter in EmailIngestService/EntityResolver leaks cross-tenant with no DB backstop. Compounding: `--org` accepts any UUID, `connector_connection` has no FK to organizations, and the script has no `is_production` guard — a typo'd org id mints a phantom tenant whose PII is invisible to the erasure path (which anchors on the organizations row).
*Evidence:* `async with GlobalSessionLocal() as session: connection = await _get_or_create_connection(session, org_id, mailbox)`
*Verdict:* CONFIRMED. Dev-only, app-layer filters intact today, but violates the project's hardest documented invariant.

**`backend/tests/identity/routes/test_erasure_routes.py:142` — no cross-tenant containment test for GDPR erasure at any layer.**
Every erasure test seeds exactly one org (per-test TRUNCATE guarantees no tenant B exists), so the org-scoping of `delete_all_in_org`/`delete_for_org_users`/`scrub_decider_emails` is never verified.
*Failure:* Dropping `.where(User.org_id == org_id)` from `delete_all_in_org` would make `POST /orgs/{A}/erase` hard-delete EVERY tenant's users/tokens, and the suite stays green. ErasureService runs on the plain platform session (RLS-exempt by design) so the app-level org filter is the sole containment — and it is untested.
*Evidence:* `assert await _count(db_session, "SELECT count(*) FROM users WHERE org_id=:o", org.id) == 0`
*Verdict:* CONFIRMED. The project's hardest testing rule, unmet on its most destructive endpoint.

**`frontend/src/admin/CreateUserDrawer.tsx:127` — closing the create drawer mid-submit destroys the one-time password and ghosts a stale success panel.**
None of the three close paths (backdrop, ✕, Escape) is gated on `submitting`; the in-flight POST's `setResult(created)` lands after `resetForm()` wiped `password`. The component stays mounted across close/reopen.
*Failure:* Slow network: admin clicks Add user, then clicks the full-screen backdrop / hits Escape → `onCreated` skipped (list stays stale), `password` cleared; the POST then succeeds and the next "+ Add user" renders `CreateUserSuccess` with a blank initial-password copy field. `UpdateUserRequest` has no password field, so the account's password is unrecoverable from this console and its email is permanently 409-taken on recreate.
*Evidence:* `setResult(created); // swap to the credential hand-off; password stays for the copy field`
*Verdict:* CONFIRMED.

**`frontend/src/identity/DevCredentialsPanel.tsx:25` — real seeded credentials (incl. the platform-admin password) hardcoded and rendered unconditionally on the login page, no DEV gate.**
`LoginPage.tsx:179` renders `<DevCredentialsPanel/>` with no `import.meta.env.DEV` guard; the panel hardcodes `super@ethera.ai / Sup3r-Dev-Only-2026!` (seeded verbatim by `seed_identity.py`).
*Failure:* Any `vite build` (staging/demo, or a missed FIX_BEFORE_PROD checklist step) ships click-to-fill platform-admin (all orgs) credentials on the public login screen. The only safeguard is a human remembering to delete a file.
*Evidence:* `password: "Sup3r-Dev-Only-2026!",`
*Verdict:* CONFIRMED. One-line `{import.meta.env.DEV && ...}` makes prod builds safe by construction.

---

### MEDIUM (40)

**`backend/app/identity/services/erasure_service.py:108` — sudo re-auth gate on erasure never checks `admin.is_active`.**
*Failure:* A deactivated platform admin, within their ≤15-min access-token TTL (no denylist), passes `get_by_id` + `verify_password` and erases an org. Every other admin-loading path checks `is_active`; the erasure re-auth — whose purpose is to re-verify the actor — omits it.
*Evidence:* `admin = await self._platform_admins.get_by_id(actor.subject_id)` / `if admin is None or not verify_password(...)`
*Verdict:* CONFIRMED. Fix: add `or not admin.is_active`.

**`backend/app/identity/services/user_service.py:73` — `create_user` global `email_exists` pre-check is a cross-tenant membership oracle (AUD-04).**
*Failure:* A company_admin POSTs `/users` with `cfo@competitor.example`; 409-vs-201 reveals whether that person is a user in ANY other tenant. Repeatable at scale, no rate limit. Violates the "never reveal existence in another org" invariant.
*Evidence:* `if await self._users.email_exists(payload.email): raise DuplicateUserError(...)`
*Verdict:* CONFIRMED. Tracked-but-deferred; rate-limiting also absent.

**`backend/app/identity/services/platform_auth_service.py:90` — platform-admin auth writes zero audit_log events.**
*Failure:* Credential-stuffing or a successful breach of `POST /platform/login` leaves no `auth.login.failure`/success rows, so a compromised god-mode console login is invisible to incident response. The less-privileged company domain audits all auth events.
*Evidence:* `if admin is None or not admin.is_active or not password_ok: raise InvalidCredentialsError(...)`
*Verdict:* CONFIRMED. Tracked (PC-04a) but live.

**`backend/app/identity/dependencies.py:141` — erasure doesn't invalidate in-flight company tokens; `offboarded` is never gated on tenant routes.**
*Failure:* After erase at T0, a company_admin's access token (issued T0-5min) calls `POST /users` within the 15-min TTL — all gates pass and a fresh PII-bearing user is inserted into a certified-erased tenant. That user can then log in indefinitely (`_load_loginable_org` blocks only `suspended`). The access-token denylist item omits erasure/offboarding.
*Evidence:* `async with scoped_session(principal.org_id) as session:` — no org-status gate.
*Verdict:* CONFIRMED.

**`backend/app/connectors/imap/fetch_session.py:157` — LIST responses with a literal mailbox name are mis-parsed; the real folder is silently never synced.**
*Failure:* A folder named e.g. `Projects "Q1"` is sent by Dovecot as a literal `(prefix, name)` tuple; the code takes `item[0]`, captures `'{12}'` as the name, `select_folder('{12}')` fails → folder skipped with no error, no cursor — all its mail permanently excluded, defeating never-lose-mail. (`_imap_quote` was added for exactly such names but they never reach it.)
*Evidence:* `line = item if isinstance(item, bytes) else item[0]`
*Verdict:* CONFIRMED (reproduced).

**`backend/app/connectors/imap/fetch_session.py:241` — non-SSL path sends LOGIN in cleartext, never attempts STARTTLS (verify path identical at `client.py:71`).**
*Failure:* An admin configures the standard port-143 STARTTLS deployment with `use_ssl=false` (the only non-SSL option); every verify and sync transmits the mailbox password unencrypted. Contradicts the "TLS in transit" rule.
*Evidence:* `return imaplib.IMAP4(params.host, params.port, timeout=socket_timeout)`
*Verdict:* CONFIRMED. Default is SSL-on, so requires explicit opt-out + on-path attacker.

**`backend/app/connectors/imap/sync/imap_fetcher.py:67` — no special-use (RFC 6154 \Junk/\Trash) or name filtering; spam and deleted mail ingested into Layer-1 + entity graph.**
*Failure:* Junk/Trash bodies become email_message rows feeding Ask retrieval; spam senders mint Person/Company entities (most pass the automation heuristics); intentionally-deleted mail is resurrected as company memory. On Gmail, `[Gmail]/All Mail` roughly double-counts the progress denominator.
*Evidence:* `for folder in await session.list_folders():`
*Verdict:* CONFIRMED. Also a latent indirect-prompt-injection surface.

**`backend/app/connectors/imap/services/email_ingest_service.py:146` — recipient resolution is uncapped; one bulk email mass-mints Person rows.**
*Failure:* One inbound newsletter/spam with several hundred To/Cc addresses issues 1000+ serialized queries inside the per-email transaction and pollutes the person graph with strangers — none gated by the sender-only automation flag.
*Evidence:* `person_id = await self._resolver.resolve_participant(`
*Verdict:* CONFIRMED.

**`backend/app/connectors/sync/connector_sync_runner.py:319` — the promised dead-letter/step-over mechanism does not exist; a poison email wedges its folder cursor forever.**
*Failure:* One deterministically-failing email at UID 100 of a 50k-message INBOX stalls `last_seen_uid` at 99 permanently; every later sync re-downloads UIDs 100→50000 (GBs repeatedly), re-counts them, re-skips via dedup. `failed_uids` is in no API and there's no drain tooling. The comment claims it "dead-letters only after a second failure" — false.
*Evidence:* `# ... dead-letters it only after a second failure (never a silent drop).`
*Verdict:* CONFIRMED. No data loss, but unbounded recurring IMAP cost + false load-bearing comment.

**`backend/tests/connectors/sync/test_sync_persistence.py:118` — sync cursor/run repos have zero cross-tenant negative tests for any method.**
*Failure:* Dropping `org_id == org_id` from `finalize()`/`mark_stale_running_abandoned()`/cursor `list_for_connection` passes the suite green (conftest uses BYPASSRLS GlobalSessionLocal). RLS + TenantMixin still backstop production, so the confirmed defect is the regression-net gap, not a live leak.
*Evidence:* `await repo.upsert(org, connection.id, "INBOX", uidvalidity=10, last_seen_uid=5, failed_uids=[3])`
*Verdict:* CONFIRMED (severity adjusted to medium — RLS backstop).

**`backend/app/connectors/security/credential_cipher.py:75` — passphrase key material stretched with a single unsalted SHA-256 (a fast hash, not a KDF); `require_secure` only checks length ≥ 32.**
*Failure:* An operator sets a memorable 32+-char phrase (passes the check); a DB/backup exfiltration lets an attacker brute-force it offline at GPU SHA-256 speed and decrypt `secret_ciphertext` for ALL orgs. The documented `openssl rand -base64 32` path is safe (bypasses SHA-256); the gate accepting weak passphrase material is the defect.
*Evidence:* `return hashlib.sha256(material.encode("utf-8")).digest()`
*Verdict:* CONFIRMED. Fix: PBKDF2/scrypt/Argon2, or reject non-base64-32 material in secure envs.

**`backend/app/core/database.py:84` — `scoped_session` sets the tenant ContextVar but never resets it (no token/finally); the ambient seam fails open.**
*Failure:* A future task that awaits `scoped_session` for multiple orgs inline leaves org A bound after A's session closes; a later `get_current_org()` (the documented seam for audit/background work) silently returns stale org A instead of raising `TenantContextMissingError`. `RequestContextMiddleware` in the same package resets its token in a finally. No production consumer of `get_current_org()` exists today.
*Evidence:* `set_current_org(org_id)` / `async with TenantSessionLocal() as session: _bind_tenant_scope(session, org_id); yield session`
*Verdict:* CONFIRMED. One-line fix (capture + reset token).

**`backend/app/entities/repositories/person_repository.py:75` — `backfill_display_name`/`extend_seen_window` (the only two UPDATE paths in entities) have no cross-tenant negative tests.**
*Failure:* Dropping `Person.org_id == org_id` from either WHERE would let a caller holding another org's person UUID overwrite that org's display_name/seen-window, with no failing test. Enforced RLS is the production backstop.
*Evidence:* `async def backfill_display_name(self, org_id: UUID, person_id: UUID, display_name: str) -> None:`
*Verdict:* CONFIRMED.

**`backend/scripts/provision_roles.py:67` — runtime-role passwords sent as a plaintext SQL literal in `ALTER ROLE`, logged verbatim under `log_statement='ddl'`/`'all'`.**
*Failure:* Managed/audited Postgres commonly logs DDL; this script runs on every container boot, so `oneai_app`/`oneai_global` passwords land in cleartext in the DB log on every restart — recoverable by anyone with log access. Violates "secrets never in logs."
*Evidence:* `await conn.execute(f'ALTER ROLE "{role}" WITH LOGIN PASSWORD {_quote_literal(password)}')`
*Verdict:* CONFIRMED. Fix: `SET LOCAL log_statement='none'`, or provision a pre-computed SCRAM verifier.

**`backend/app/db/migrations/versions/0009_enforce_rls.py:88` — `GRANT ... ON ALL TABLES` over-privileges `oneai_app` on non-RLS tables (refresh_tokens, platform_admins, organizations, alembic_version).**
*Failure:* Any SQLi/raw-query bug on a tenant-engine path escalates cluster-wide: SELECT all `platform_admins.password_hash`/`refresh_tokens.token_hash`, or INSERT a `refresh_tokens` row with `subject_type='platform_admin'` and redeem it to mint platform-admin tokens; UPDATE `alembic_version` corrupts the ledger. None of these tables is row-filtered.
*Evidence:* `op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")`
*Verdict:* CONFIRMED. Least-privilege violation.

**`backend/app/db/migrations/versions/0005_audit_log.py:68` — `audit_log` (org-attributed actor_email + ip_address) has no RLS policy, and 0009 grants the tenant role full SELECT on it.**
*Failure:* The no-policy choice is documented for INSERT only; the blanket grant leaves SELECT wide open. The first tenant-facing "view my org's audit log" feature that misses an org_id filter returns every org's audit PII with no DB backstop.
*Evidence:* `sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True)` (+ docstring "NOT under any RLS policy")
*Verdict:* CONFIRMED. Fix: `INSERT WITH CHECK (org_id = GUC OR org_id IS NULL) + SELECT USING (org_id = GUC)`. (Two findings overlap on this grant — see also the low-tier audit_log entry.)

**`backend/app/db/migrations/env.py:36` — raw DB URL (with password) passed to `set_main_option` without `%`-escaping; a `%` in the password crashes every migration.**
*Failure:* Rotating `POSTGRES_PASSWORD` to a generated secret containing `%` (or URL-encoded `%xx`) makes `alembic upgrade head` die with an interpolation error before any migration runs, blocking deploys. Reproduced in the repo venv.
*Evidence:* `config.set_main_option("sqlalchemy.url", get_settings().database_url)`
*Verdict:* CONFIRMED. Fix: `.replace("%", "%%")`.

**`backend/scripts/seed_identity.py:117` — demo-backdoor seed guard refuses only `app_env == 'production'`, while the config's secret guard treats every non-local/test env as sensitive.**
*Failure:* A properly-secreted staging deploy (or a typo'd `APP_ENV="prod"`) seeds `super@ethera.ai`/`admin@demo.oneai` whose passwords are published in this VCS-tracked file — one-click platform/company admin to staging data.
*Evidence:* `if get_settings().is_production: raise SystemExit(...)`
*Verdict:* CONFIRMED. Fix: gate on `requires_secure_secrets`.

**`frontend/src/platform/OnboardCompanyDrawer.tsx:129` — drawer closable mid-submit; late `setResult` lands after `resetForm`, ghosting an empty-password success panel.** (Same class as the high CreateUserDrawer finding; the temp password here is operator-typed, so usually re-typeable → medium.)
*Failure:* Backdrop/Escape during the in-flight onboard POST → `onOnboarded` skipped (list not refreshed), `adminPassword` cleared; POST succeeds, success screen shows blank password on reopen, re-submit hits a confusing 409.
*Evidence:* `setResult(onboarded);` after `await onboardCompany(...)` with no closed-guard.
*Verdict:* CONFIRMED.

**`frontend/src/platform/AuditTrail.tsx:81` — reload refetch doesn't cancel/ignore the in-flight previous fetch; a stale response can overwrite the fresh one (violates the file's own AC8).**
*Failure:* Slow initial trail GET (e.g. stuck in 401-refresh retry); operator clicks Suspend → reloadSignal bumps → second GET resolves first with the new row → the original GET resolves and overwrites with pre-suspend rows. The audit event the operator just caused is missing on a compliance surface.
*Evidence:* `setEntries(await getOrganizationAudit(orgId, TRAIL_LIMIT));` with no stale guard.
*Verdict:* CONFIRMED.

**`frontend/src/platform/OrgErasurePanel.tsx:108` — compliance-critical 409 (legal hold) / 403 (wrong password) error mapping has no test; the component lacks its mirroring test file.**
*Failure:* Swapping the 403/409 branches ships unnoticed — an admin blocked by a legal hold sees "Incorrect password" (or the generic message) instead of the legal-hold explanation, on a GDPR-vs-legal-hold surface, with zero failing tests. `handleExport` error/download paths equally untested.
*Evidence:* `status === 409 ? "This company is under legal hold ..." : status === 403`
*Verdict:* CONFIRMED.

**`frontend/src/admin/CreateUserSuccess.tsx:32` — `CopyField` reports "Copied" without awaiting/checking `navigator.clipboard?.writeText`; a missing/rejected clipboard still claims success.** (Duplicated in `platform/OnboardSuccess.tsx:26`.)
*Failure:* Plain-HTTP LAN host (non-secure context → `navigator.clipboard` undefined) or lost focus: writeText is a no-op/rejects, the button still flips to "Copied", the admin clicks Done trusting the one-time password is on the clipboard. The rejection is an unhandled promise rejection.
*Evidence:* `void navigator.clipboard?.writeText(value); setCopied(true);`
*Verdict:* CONFIRMED.

**`frontend/src/admin/CreateUserDrawer.tsx:265` — no per-field validation feedback; a password within 8–128 chars but over bcrypt's 72-UTF-8-byte cap keeps submit disabled while the hint says requirements are met.**
*Failure:* A DACH admin types a 40-char umlaut passphrase (80 bytes): `isValidPassword` fails on bytes, "Add user" stays disabled with no explanation, the hint "8–128 characters" says the input is fine. Same silence for invalid email or 201-char name.
*Evidence:* hint reads "8–128 characters. You hand it to the user after creating ..." (no byte-cap mention).
*Verdict:* CONFIRMED. (A visible Generate button is the one escape hatch.)

**`frontend/src/identity/authClient.ts:77` — the 7-day company refresh token is persisted in localStorage (XSS-readable).**
*Failure:* For a product that renders ingested third-party email content, a single future XSS reads `localStorage.getItem('oneai.refresh_token')` and exfiltrates a 7-day sliding credential redeemable off-box; single-use rotation only logs out the legitimate tab. The platform token is correctly memory-only; the httpOnly-cookie mitigation for the company token is not used. (No XSS sink exists today → conditional.)
*Evidence:* `localStorage.setItem(REFRESH_STORAGE_KEY, tokens.refresh_token);`
*Verdict:* CONFIRMED.

**`frontend/src/connect/AddMailboxDrawer.tsx:103` — closing the drawer mid-submit orphans a server-created connection; re-submit hits a confusing 409.**
*Failure:* `createConnection` succeeds immediately, then `testConnection` blocks on the IMAP connect (up to 15s). Impatient backdrop/Escape → `onConnected` skipped, list never refreshes; the mailbox exists but is invisible, and re-entering it returns 409 "already connected" — a dead end until a manual reload.
*Evidence:* `function handleClose(): void { const created = result !== null; resetForm(); if (created) onConnected();`
*Verdict:* CONFIRMED.

**`frontend/src/connect/AddMailboxDrawer.tsx:1` — the credential-entry drawer (most security-sensitive FE surface) has no test file.**
*Failure:* Untested: create-then-test sequencing, 409/503/generic `messageFor` mapping, 401/403 → `onSessionExpired`, host/port auto-fill vs override, and the mid-submit-close bug above. A regression (untrimmed email-as-username, dropped 503 message) ships silently. Every named sibling has a mirrored test.
*Evidence:* file header `Role: Slide-in drawer to connect a mailbox.`
*Verdict:* CONFIRMED.

**`frontend/src/App.tsx:57` — no catch-all (`path="*"`) route; any unmatched URL renders a blank screen.**
*Failure:* A typo'd URL / stale bookmark / renamed route renders only the animated body gradient — no content, no nav, no recovery except editing the URL.
*Evidence:* `<Routes location={location} key={location.pathname}>` — six routes, no fallback.
*Verdict:* CONFIRMED.

**`frontend/src/pageTransition.ts:61` — back navigation: the OUTGOING screen exits with the forward exit instead of mirroring.**
*Failure:* On every pop, the exiting screen is frozen inside `<Routes location={oldLocation}>` whose LocationContext still holds the forward state (verified against react-router 7.16), so `goingBack` can't flip; the two screens slide in opposite directions, breaking the documented "back mirrors it" invariant. Cosmetic.
*Evidence:* `const goingBack = (useLocation().state as { nav?: "back" } | null)?.nav === "back";`
*Verdict:* CONFIRMED.

**`backend/tests/identity/services/test_user_service.py:200` — `lock_active_admin_ids` (the last-admin guard) has no cross-org negative test.**
*Failure:* Dropping `User.org_id == org_id` makes the guard count other orgs' admins; org A's only admin no longer triggers `LastAdminError` at the app layer, with no failing test. Enforced RLS contains the single-mutation blast radius in production → medium.
*Evidence:* `with pytest.raises(LastAdminError): await service.deactivate_user(org.id, admin.id, _ACTOR)`
*Verdict:* CONFIRMED.

**`backend/tests/identity/test_dependencies.py:42` — the tenant-seam test asserts only the GUC, never that the session runs as the non-BYPASSRLS tenant role.**
*Failure:* A refactor switching `get_tenant_session`/`scoped_session` to `GlobalSessionLocal` while still calling `set_config` passes this test and all cross-tenant route tests (app-level filters still isolate) — silently removing the RLS layer for every tenant request. Fix: assert `SELECT current_user == app role`.
*Evidence:* `bound = (... "SELECT current_setting('app.current_org_id', true)").scalar(); assert bound == str(org_id)`
*Verdict:* CONFIRMED.

**`backend/tests/identity/routes/test_support_routes.py:67` — no member-role (403) negative tests for any company `/support-access` endpoint.**
*Failure:* Rewiring `POST /support-access/{id}/approve` (or inbox/deny/revoke) from `require_company_admin` to a plain authenticated dependency lets any member consent to break-glass platform access over the whole org — and the suite stays green (no member token is ever sent to a company support endpoint). `/users` has a member-403 test per endpoint.
*Evidence:* `def _company_headers(...): return bearer(company_token(user_id, org_id, UserRole.company_admin))`
*Verdict:* CONFIRMED.

**`backend/tests/identity/services/test_user_service.py:179` — mock-boundary violation: monkeypatches the internal repo method `email_exists`.**
*Failure:* testing.md forbids mocking internal repos. If `create_user` switches to a different pre-check, the `_always_false` stub patches dead code and the test keeps passing while asserting nothing about the real IntegrityError→409 mapping. The same TOCTOU race is provable mock-free via two concurrent `scoped_session` transactions (pattern already in this file).
*Evidence:* `monkeypatch.setattr(repo, "email_exists", _always_false)`
*Verdict:* CONFIRMED.

**`backend/tests/connectors/sync/runner/test_connector_sync_runner.py:1` — no crash-mid-run test for `ConnectorSyncRunner`.**
*Failure:* Nothing makes the fake connector raise during `count_pending` or mid-stream, so `run()`'s catch-all → `_finalize_failure` (fresh-session finalize to `sync_status='error'` + ledger `failed`) has zero coverage. A regression there strands the connection `running` until the 5-min reclaim, suite green. Batch-committed-before-crash survival also untested.
*Evidence:* file header describing the covered scenarios (crash-mid-run absent).
*Verdict:* CONFIRMED.

**`backend/app/connectors/sync/connector_sync_runner.py:75` — `_is_dedup_collision` and the runner-level `failed` outcome have no test.**
*Failure:* A migration renaming `uq_email_message_dedup` (or asyncpg changing the constraint-name shape) reclassifies every benign dedup collision as `failed`, stalling the folder cursor — suite green. If `_ingest_batch` ever accounted a failed UID, mail would silently drop with no integration test asserting `messages_failed`/`failed_uids` land in the DB.
*Evidence:* `def _is_dedup_collision(error: IntegrityError) -> bool:`
*Verdict:* CONFIRMED.

**`backend/app/connectors/repositories/connector_sync_run_repository.py:81` — `list_for_connection` is dead code (zero call sites) and untested.**
*Failure:* Every `list_for_connection` usage belongs to the cursor repo; this run-repo variant is never invoked. If a future run-history endpoint wires it, it ships with no coverage incl. no cross-tenant test. Violates A5 (no dead code) + the testing rule.
*Evidence:* `async def list_for_connection(self, org_id: UUID, connection_id: UUID, limit: int = 20) -> list[ConnectorSyncRun]:`
*Verdict:* CONFIRMED.

**`backend/app/connectors/sync/connector_sync_runner.py:206` — open DB transaction held across `count_pending()`'s full IMAP folder enumeration.**
*Failure:* On a large mailbox, `count_pending` takes minutes of LIST/SELECT/SEARCH round-trips while the tenant session sits idle-in-transaction; with `idle_in_transaction_session_timeout` (standard managed-Postgres hardening) the backend is killed mid-wait → the run fails on `set_total`. Even without it, one of 15 pooled connections is pinned per running sync. (The per-batch path correctly commits first.)
*Evidence:* `total = await connector.count_pending(fetch_cursors); if not await connections.set_total(...)`
*Verdict:* CONFIRMED. Fix: commit/rollback before `count_pending`.

**`backend/app/connectors/services/connector_service.py:133` — `test_connection` holds the request transaction open across the 15s IMAP verify.**
*Failure:* A handful of admins testing connections against slow/unreachable hosts (each pinned ~15s) exhausts the default tenant pool (5+10); unrelated tenant requests then block on the 30s `pool_timeout` and 500. The pool is process-wide across all orgs.
*Evidence:* `connection = await self.get_connection(org_id, connection_id); check = await self._verify(connection)`
*Verdict:* CONFIRMED.

**`backend/app/connectors/imap/services/email_ingest_service.py:79` — synchronous `parse_email` (RFC822 parse, base64 attachment decode, sha256, html2text) runs on the event loop inside the background sync task.**
*Failure:* During a tenant's in-process sync, each large/HTML/attachment-heavy email costs hundreds of ms–seconds of pure CPU on the shared loop, stalling every other tenant's HTTP requests in repeated bursts for the duration of an initial import. `parse_email` is a documented pure function — safe to `asyncio.to_thread`.
*Evidence:* `parsed = parse_email(raw_bytes, self._mailbox, internal_date)`
*Verdict:* CONFIRMED.

**`frontend/src/admin/adminClient.ts:21` — no shared HTTP-client module; the base-URL fallback and `parseJsonOrThrow` are copy-pasted across all five clients + HomePage.**
*Failure:* A base-URL strategy change (path prefix, env-var rename) or surfacing the backend `detail` body requires editing 6 files; missing one (e.g. HomePage's private `API_URL`) silently points that surface at `http://localhost:8000` in a deployed build — health shows "backend unreachable" despite a working API.
*Evidence:* `const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000"; async function parseJsonOrThrow<T>(...)`
*Verdict:* CONFIRMED.

**`frontend/src/platform/OnboardSuccess.tsx:56` — onboard/connect success panels don't re-assert focus after the form→success swap; the dialog focus trap dies.**
*Failure:* A keyboard/screen-reader operator submits; the focused submit button unmounts, focus drops to `<body>`, the container-scoped keydown handler stops receiving events — Escape stops closing, Tab walks the background page, nothing is announced — precisely while one-time credentials are on screen. `CreateUserSuccess` documents and fixes exactly this; two of three panels lack the fix.
*Evidence:* `OnboardSuccess({ company, plaintextPassword, onDone })` — no `headingRef`/`focus()` on mount.
*Verdict:* CONFIRMED.

---

### LOW (47)

**`backend/app/identity/services/platform_org_service.py:69` — `set_status` has no lifecycle state machine: an `offboarded` (erased) org can be flipped back to `active`, and `offboarded` can be set without running erasure.**
*Failure:* PATCH `status='active'` on an erased org presents it as a live customer contradicting its deletion certificate; PATCH `status='offboarded'` on a live org makes it appear erased while all PII + login access remain.
*Evidence:* `from_status = organization.status; organization.status = status.value`
*Verdict:* CONFIRMED.

**`backend/app/identity/services/auth_service.py:144` — on refresh, a suspended-org/deactivated-user rejection raises AFTER `TokenRotator.consume()`, so the rollback un-revokes the consumed token; no `LOGIN_BLOCKED`-style audit row.**
*Failure:* Suspended org's client calls `/auth/refresh`; consume() revokes, the org gate raises, the session rolls back — the token stays live and works on reactivation, and the attempt leaves zero audit trail (the equivalent login writes `auth.login.blocked`).
*Evidence:* `# long-lived refresh token can't outlive a suspension.` / `await self._load_loginable_org(user.org_id)`
*Verdict:* CONFIRMED.

**`backend/app/identity/services/erasure_service.py:171` — `export_compliance` silently caps the "full audit trail" at 1000 entries with no truncation indicator.**
*Failure:* A busy org's regulator export drops everything past the newest 1000 rows with no `truncated` flag/count/cursor in the schema — the consumer can't detect the omission. Acknowledged only in a code comment.
*Evidence:* `audit = await self._audit.list_for_org(org_id, limit=_MAX_EXPORT_ENTRIES, offset=0)`
*Verdict:* CONFIRMED.

**`backend/app/identity/routes/platform_routes.py:183` — `GET /platform/orgs/{id}/audit` returns 200 `[]` for a nonexistent org instead of 404 (siblings 404).**
*Failure:* A typo'd/stale org UUID yields an empty trail indistinguishable from "exists but no history"; a compliance script silently produces an empty trail instead of failing loudly. `get_detail` and `compliance-export` both 404 the same UUID.
*Evidence:* `return await service.list_for_org(org_id, limit=limit, offset=offset)`
*Verdict:* CONFIRMED.

**`backend/app/identity/schemas/support_schemas.py:26` — `reason` (and `ErasureRequest.reason`) lack the control-char rejection (DYN-03) that `SafeName` applies.**
*Failure:* A platform admin submits `reason="urgent\x1b[2J\x07 fix"` — C0 control chars pass validation and render verbatim in the company approval inbox and audit-detail exports (ANSI/BEL injection); a NUL byte degrades to a generic 422 instead of the field-level 422 SafeName produces.
*Evidence:* `reason: str = Field(min_length=1, max_length=500)`
*Verdict:* CONFIRMED.

**`backend/app/identity/schemas/user_schemas.py:101` — no password-change field and no reset endpoint anywhere; a forgotten password permanently locks the account and consumes its globally-unique email.**
*Failure:* `PATCH /users/{id}` offers only name/role/is_active; DELETE soft-deactivates (row kept); recreating with the same email returns 409 forever. Recovery requires direct DB surgery.
*Evidence:* `full_name: SafeName | None = None; role: UserRole | None = None; is_active: bool | None = None`
*Verdict:* CONFIRMED.

**`backend/app/identity/error_handlers.py:81` — all 401 responses omit the `WWW-Authenticate: Bearer` challenge header (RFC 6750/7235).**
*Failure:* OAuth2-aware clients/gateways/SDKs keyed on the header can't apply standard challenge semantics; Swagger interop degrades. First-party React keys on status codes → cost is third-party/standards hygiene.
*Evidence:* `return JSONResponse(status_code=status_code, content={"detail": str(exc)})`
*Verdict:* CONFIRMED.

**`backend/app/connectors/imap/parsing/models.py:13` — stale Key-invariants docstring claims `dedup_key` is ALWAYS sha256 of raw RFC822 bytes; `_dedup_key` now hashes Message-ID + headers + body.**
*Failure:* Per A4 (docstrings as runtime input), a reader reasons from the false security rationale ("a planted decoy can't suppress a genuine email") and may revert the folder-stable key. The two in-repo docstrings contradict each other.
*Evidence:* `- `dedup_key` is ALWAYS a sha256 of the RAW rfc822 bytes — never the attacker-influenceable`
*Verdict:* CONFIRMED.

**`backend/app/connectors/sync/connector_sync_runner.py:218` — disable-mid-sync breaks the loop then finalizes `ok=True`, stamping the run `succeeded` + `last_synced_at=now()`.**
*Failure:* Admin disables at message 500/100000; the ledger reads `succeeded`, the connection shows "last synced just now", but 99,500 messages were never ingested and nothing schedules a follow-up. The run-ledger model's own docstring says "the audit never lies about a crashed run."
*Evidence:* `if await connections.is_disabled(org_id, connection_id): break`
*Verdict:* CONFIRMED.

**`backend/app/connectors/sync/connector_sync_runner.py:354` — `_stop_ticker` suppresses `CancelledError` while awaiting the cancelled ticker, swallowing cancellation of the runner task itself.**
*Failure:* Uvicorn shutdown cancels the sync task while awaiting the ticker in `finally`; the CancelledError is suppressed, the task proceeds into `_finalize` and issues DB writes against a disposing engine instead of exiting promptly. `_heartbeat_loop` re-raises CancelledError, confirming this is an omission.
*Evidence:* `with contextlib.suppress(asyncio.CancelledError, Exception): await ticker`
*Verdict:* CONFIRMED.

**`backend/app/connectors/models/connector_sync_cursor.py:12` — model docstring states the opposite of implemented behavior: claims `failed_uids` are "stepped over" while the runner deliberately stalls forever (migration 0011 repeats the false claim).**
*Failure:* Per A4, a maintainer "restoring" the documented step-over (adding failed_uids to `accounted`) would advance the cursor past never-stored mail — the exact silent-loss regression `test_persistent_failure_never_steps_over_the_uid` exists to prevent.
*Evidence:* `- `failed_uids` ... so the contiguous-prefix advance steps OVER them instead of wedging the folder forever`
*Verdict:* CONFIRMED.

**`backend/app/connectors/sync/connector_sync_runner.py:310` — `_ingest_one` returns `IngestOutcome | str` using the magic literal `"failed"`; the enum docstring references a nonexistent `FAILED` member.**
*Failure:* A future typo'd literal or a real `IngestOutcome.FAILED` comparison passes mypy and runs silently (the union collapses to `str` since `IngestOutcome` is a StrEnum). Adding `FAILED` makes the contract checkable.
*Evidence:* `) -> IngestOutcome | str:` / `"""... returns the IngestOutcome, or 'failed' if poison."""`
*Verdict:* CONFIRMED.

**`backend/app/main.py:70` — `MaxBodySizeMiddleware` is outermost, so its 413 never passes through `CORSMiddleware` and carries no ACAO header.**
*Failure:* A `:5173` POST over 1 MiB gets a 413 with no CORS header; the browser blocks the response and `fetch()` rejects with opaque "Failed to fetch" — the UI can never show "Request body too large." No current flow sends >1 MiB.
*Evidence:* `app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_request_body_bytes)`
*Verdict:* CONFIRMED.

**`backend/app/entities/services/entity_resolver.py:157` — dedup-by-catch on every sighting: `_enrich_person`/`_link_person_company` unconditionally INSERT-and-catch instead of SELECT-first.**
*Failure:* Bulk ingest of a 50k mailbox from known senders performs ~2 savepoints + 2 deliberately-failing INSERTs + 2 rollbacks per participant per message; each aborted INSERT leaves a dead heap tuple → person_alias/person_company bloat + 4 extra round trips per participant. `_get_or_create_*` correctly SELECT-first; these paths don't.
*Evidence:* `async with self._session.begin_nested(): await self._people.add_alias(...) except IntegrityError: pass`
*Verdict:* CONFIRMED.

**`backend/app/main.py:52` — `_handle_data_error` maps every DB `DataError` to 422 without logging the underlying exception.**
*Failure:* A server-originated bad value (column-width mismatch after a refactor) raises asyncpg `DataError`; the client gets a generic 422 and ops gets no stack trace/log line — the code defect is misdiagnosed as bad client data. The codebase already documents this class of server-side DataError elsewhere.
*Evidence:* `return JSONResponse(status_code=422, content={"detail": "Invalid input value."})`
*Verdict:* CONFIRMED. One-line fix (`logger.warning(..., exc_info=True)`).

**`backend/app/db/migrations/versions/0009_enforce_rls.py:127` — downgrade's `DROP OWNED BY` revokes only in the current DB, so `DROP ROLE` fails if 0009 also ran against a second DB on the cluster.**
*Failure:* A cluster with `oneai` + `oneai_test` both at head: `downgrade 0008` runs `DROP OWNED BY` (current DB only) then `DROP ROLE`, which errors on the other DB's privileges, rolling back the whole downgrade. Repo topology is single-DB-per-cluster, so the precondition isn't created today.
*Evidence:* `EXECUTE 'DROP OWNED BY {role}'; EXECUTE 'DROP ROLE {role}';`
*Verdict:* CONFIRMED.

**`backend/scripts/ingest_imap_dump.py:35` — stale comment claims demo seed orgs have fixed ids `00000000-…-0001/0002`; `seed_identity.py` uses random `gen_random_uuid()`.**
*Failure:* A developer reasons about collision-safety / writes tooling targeting `00000000-…-0001` from a false premise, on the exact seam (org targeting) where a mistake writes into the wrong tenant.
*Evidence:* `# A fixed DEV org for disk ingests — distinct from the demo seed orgs (00000000-…-0001/0002).`
*Verdict:* CONFIRMED.

**`frontend/src/platform/onboardValidation.ts:42` — client validation omits the 200-char name caps and bcrypt's 72-UTF-8-byte password cap (allows up to 128 chars).**
*Failure:* A 90-char password or >200-char company name passes the client check, the backend returns 422, and the catch shows the generic connectivity message — the exact outcome the module's "mirrors server bounds" invariant forbids.
*Evidence:* `password.length >= 8 && password.length <= 128`
*Verdict:* CONFIRMED.

**`frontend/src/admin/useCompanyUsers.ts:171` — `busyUserId` is a single shared value; each mutation's `finally` clears it, re-enabling a different row whose request is still in flight.**
*Failure:* Change Bob's role then immediately change/reactivate Carol: Bob's PATCH resolves first, its finally clears `busyUserId`, re-enabling Carol's row mid-flight → a second click sends a duplicate/conflicting request, breaking the documented disabled-while-busy invariant.
*Evidence:* `} finally { setBusyUserId(null); }`
*Verdict:* CONFIRMED.

**`frontend/src/admin/ConfirmDialog.tsx:101` — Cancel/Escape not disabled while `busy`; a subsequent 409 is written to a no-longer-rendered dialog and silently swallowed.**
*Failure:* Admin confirms self-deactivate (last admin), then cancels during the in-flight DELETE → dialog unmounts → the 409 sets `confirmError` into the void → the admin gets no feedback the deactivation failed. The request isn't aborted either.
*Evidence:* `<button type="button" onClick={onCancel}` (no `disabled`).
*Verdict:* CONFIRMED.

**`frontend/src/connect/AddMailboxDrawer.tsx:234` — the app-password input uses `autoComplete="off"`, which Chromium ignores for password fields.**
*Failure:* Chrome offers to save the mailbox app-password against the One AI origin, then autofills it as the One AI account password on `/login` (same origin, `current-password`) — credential bleed + persistence in the synced store. Repo convention elsewhere uses `new-password`.
*Evidence:* `type={showPassword ? "text" : "password"} value={password} autoComplete="off"`
*Verdict:* CONFIRMED.

**`frontend/src/identity/authClient.ts:230` — logout doesn't propagate across tabs (no `storage`/BroadcastChannel listener).**
*Failure:* On a shared machine, logout in the visible tab leaves a background tab holding a valid in-memory access token (stateless JWT) for up to the 15-min TTL; the next person reads `/auth/me`, `/connectors`, etc. from that tab.
*Evidence:* `export async function logout(): Promise<void> {`
*Verdict:* CONFIRMED. Fix: a `storage`-event listener calling `setTokens(null)`.

**`frontend/src/connect/ConnectorsPage.tsx:265` — `onConnected` calls `controller.reload()` non-silently, blanking the loaded list into skeletons (contradicts `useConnectors`' silent-reload invariant).**
*Failure:* A user with 5 mailboxes adds a 6th and clicks Done: the whole list disappears into shimmer skeletons for the round-trip, then re-renders — a content flash on every successful connect.
*Evidence:* `onConnected={() => void controller.reload()}`
*Verdict:* CONFIRMED. One-char fix: `reload(true)`.

**`frontend/src/connect/useConnectors.ts:116` — `runAction`'s failure path shows a notice but never reloads; a row whose backing connection vanished server-side persists as a stale ghost.**
*Failure:* Admin B deletes "Sales mailbox"; admin A clicks Test → 404 → generic notice, but the ghost row stays and every retry fails identically (no poll runs). A `reload(true)` in the non-auth catch branch resyncs and self-explains.
*Evidence:* `setNotice(GENERIC_MESSAGE); } finally { setBusyId(null);`
*Verdict:* CONFIRMED.

**`frontend/src/support/SupportAccessPanel.tsx:56` — loadState `"error"` is set but never rendered; failed request/revoke POSTs are swallowed with no feedback.**
*Failure:* A 500 on the GET hides all existing requests with no message, so an admin with a pending request submits a duplicate (backend inserts unconditionally — no 409); a failed POST just re-enables the button with zero indication.
*Evidence:* `setLoadState("error"); ... {loadState === "loaded" && grants.length > 0 && (`
*Verdict:* CONFIRMED.

**`frontend/src/components/insignia/renderInsignia.ts:306` — on the first RAF frame `time===0` so `born` defaults to 1; the fully-assembled emblem flashes for one frame before the particle-birth restarts.**
*Failure:* Every mount of `BrandMark` (login) / `AgentInsignia` (home card) draws the complete crest at frame 1, then frame 2 draws `born≈0.01` — a pop-then-vanish flicker that defeats "nothing pops into existence."
*Evidence:* `const animated = time > 0; ... const born = animated && assemble > 0 ? Math.min(1, time / assemble) : 1;`
*Verdict:* CONFIRMED.

**`frontend/src/components/AgentInsignia.tsx:82` — `activity` is in the render-effect deps, so any activity change tears down/restarts the RAF loop, replaying the full assemble "birth."**
*Failure:* A caller driving the live `activity` prop (idle→thinking, the component's purpose) gets the emblem dissolving and re-converging over `assembleSeconds` plus a spin snap-to-zero on every toggle. `BrandMark` fixed this with `activityRef`. Latent (no current caller passes `activity`).
*Evidence:* `}, [spec, size, activity, assembleSeconds]);`
*Verdict:* CONFIRMED.

**`frontend/src/components/AgentInsignia.tsx:80` — each insignia runs its own never-idle RAF loop at full frame rate, no shared scheduler, no off-screen pause.**
*Failure:* List screens (platform `CompanyCard` renders one `BrandMark` per company) run N independent 60fps canvas redraw loops indefinitely — CPU/battery drain scaling linearly with list size. `prefers-reduced-motion` and hidden-tab suspension cap it, but not the default visible-list case.
*Evidence:* `rafId = requestAnimationFrame(tick);`
*Verdict:* CONFIRMED.

**`frontend/src/App.test.tsx:31` — the route-shell suite never exercises `/admin`, `/connectors`, or `/platform`, so guard-wrapping is untested.**
*Failure:* Dropping `AdminRoute` from the `/connectors` element passes the entire suite (guards are tested only in isolation); an unauthenticated visit would render the connectors shell instead of redirecting. Role gating picks a screen; data access stays server-enforced → leaks a shell, not data.
*Evidence:* `describe("App route shell", () => {`
*Verdict:* CONFIRMED.

**`backend/app/identity/repositories/support_grant_repository.py:61` — no repo-layer test file; org-scoped methods covered only indirectly via route tests.**
*Failure:* `get_in_org`/`list_for_org`/`scrub_decider_emails` lack the per-layer cross-tenant negatives the rule mandates; a future non-HTTP caller (e.g. a background expiry job) gets no isolation contract test, and repo regressions surface only as full-stack route failures. The user repo got dedicated repo-layer tests; this one didn't.
*Evidence:* `async def get_in_org(self, grant_id: UUID, org_id: UUID) -> SupportGrant | None:`
*Verdict:* CONFIRMED.

**`backend/tests/core/test_tenant.py:35` — `set_current_org` called without resetting the ContextVar, leaking a bound tenant into later same-context tests.**
*Failure:* A future test expecting an unbound context (asserting `TenantContextMissingError`) passes alone but fails after this file runs — an order-dependent flake. The file already spawns a fresh OS thread to dodge its own leak.
*Evidence:* `set_current_org(org_id)` / `assert get_current_org() == org_id`
*Verdict:* CONFIRMED.

**`backend/tests/connectors/sync/test_folder_tracker.py:78` — `test_failed_signal_is_capped_safely` computes the cap inside the test and asserts on its own local computation; the production capping code is never invoked.**
*Failure:* Removing the cap or flipping to keep the HIGHEST UIDs (`[-MAX_FAILED_UIDS:]`) still passes — false coverage of the "keeps lowest UIDs" operator-signal property, and an unbounded `failed_uids` JSON array can silently grow the cursor row.
*Evidence:* `capped = sorted(tracker.failed)[:MAX_FAILED_UIDS]`
*Verdict:* CONFIRMED.

**`backend/app/db/migrations/versions/0009_enforce_rls.py:88` (audit_log facet) — `oneai_app` granted blanket SELECT/UPDATE/DELETE on `audit_log`, the one tenant-reachable table where a missed app-layer org filter has no DB backstop.**
*Failure:* The day a tenant-facing audit-trail read lands with a missed `WHERE org_id`, a tenant-engine `SELECT actor_email, ip_address FROM audit_log` returns every org's PII and nothing fails closed (unlike the 14 FORCE-RLS tables). Least-privilege for the documented need is INSERT-only.
*Evidence:* `op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")`
*Verdict:* CONFIRMED. (Read-exposure facet of the medium 0005/0009 finding above — both rooted in this grant.)

**`backend/app/identity/repositories/user_repository.py:79` — `email_exists` is the AUD-04 cross-tenant existence oracle (consumed by `create_user`).**
*Failure:* A company_admin POSTs `/users` with `bob@competitor.com` → 409 iff that address is a user in any other tenant, 201 otherwise — address-by-address enumeration of another customer's users. Returns only a boolean existence signal, no PII/content. (Same root cause as the medium `user_service.py:73` oracle.)
*Evidence:* `async def email_exists(self, email: str) -> bool: ... select(User.id).where(User.email == email)`
*Verdict:* CONFIRMED.

**`backend/app/connectors/sync/connector_sync_runner.py:410` — decrypt-secret + merge-username + `registry.create` connector-build sequence duplicated between `_build_connector` and `ConnectorService._verify` (docstring admits "mirrors service").**
*Failure:* When the config shape changes (e.g. key-version-aware decryption — `secret_key_version` is already persisted), updating `_verify` but not the runner makes test-connection pass while every sync builds from a stale config — silent divergence between verified and syncing paths.
*Evidence:* `secret = self._cipher.decrypt(...); config = {**(connection.config or {}), "username": connection.username}; return self._registry.create(...)`
*Verdict:* CONFIRMED.

**`backend/app/connectors/dependencies.py:85` — `CredentialCipher` construction copy-pasted in `get_connector_service` and `get_sync_service`.**
*Failure:* A change to the fail-closed cipher wiring (key rotation, `requires_secure_secrets` semantics) edited in one provider but not the other leaves create/test and sync paths encrypting under different policies; a single `get_credential_cipher` dependency would also give tests one override seam.
*Evidence:* `settings = get_settings(); cipher = CredentialCipher(settings.connector_secret_key, require_secure=settings.requires_secure_secrets)`
*Verdict:* CONFIRMED.

**`backend/app/connectors/error_handlers.py:54` — `_make_handler` + register loop duplicated verbatim between connectors/ and identity/ error handlers.**
*Failure:* Any hardening (logging the handled exception, sanitizing `str(exc)`, correlation id) applied to one copy silently misses the other, and the next domain pastes a third copy.
*Evidence:* `def _make_handler(status_code: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]: """Build a FastAPI exception handler ..."""`
*Verdict:* CONFIRMED.

**`backend/app/connectors/sync/connector_sync_runner.py:1` — file is 414 lines, the worst warn-band offender among app code (A2: split-soon at 300).**
*Failure:* Every edit to any sync concern pays the 414-line context tax; the file holds three separable units (the dedup helper, the `_Counts`/`_FolderTracker` dataclasses, the runner class), and a future fetcher feature pushes it toward the 500-line CI hard fail.
*Evidence:* `414 backend/app/connectors/sync/connector_sync_runner.py (wc -l)`
*Verdict:* CONFIRMED.

**`backend/app/connectors/imap/fetch_session.py:123` — `_run`, the central dispatcher of all blocking IMAP calls, is untyped with `# type: ignore[no-untyped-def]`.**
*Failure:* `func`/`*args`/return untyped → a dropped/swapped argument at any of the six call sites type-checks clean and surfaces as a runtime TypeError mid-sync. A ParamSpec signature catches it statically. (No mypy in CI today.)
*Evidence:* `async def _run(self, func, *args):  # type: ignore[no-untyped-def]`
*Verdict:* CONFIRMED.

**`backend/app/connectors/imap/sync/fetch_planner.py:78` — `decode_mutf7` (+ `_b64_to_text`, ~35 lines) is dead code: defined and unit-tested but never called; docstring claims a log usage that doesn't exist.**
*Failure:* The only references are the definition and its test; folder names are logged raw (undecoded). The "(for logs)" docstring and "Used by" line mislead readers, and the tests give false confidence the path matters. A5 forbids dead code.
*Evidence:* `def decode_mutf7(name: str) -> str: """Decode an IMAP modified-UTF-7 mailbox name (RFC 3501) to readable Unicode (for logs)."""`
*Verdict:* CONFIRMED.

**`backend/app/main.py:41` — lifespan shutdown disposes DB engines without cancelling/awaiting in-flight background sync tasks.**
*Failure:* A deploy/restart during a sync abandons the runner without finalize; the connection is left `sync_status='running'` with a stale heartbeat, so `POST /connectors/{id}/sync` returns 409 "already running" for up to `STALE_SECONDS` (300s) after the restart, and the ledger lingers `running`. No mail loss (cursor design). Crash-recovery is the deliberate baseline.
*Evidence:* `yield` / `for db_engine in (tenant_engine, global_engine, engine): await db_engine.dispose()`
*Verdict:* CONFIRMED.

**`frontend/src/admin/CreateUserDrawer.tsx:37` — `TextField` + `INPUT_CLASS` triplicated across three drawers; `CopyField` duplicated between two success panels; copies have drifted.**
*Failure:* A form-style/behavior fix (focus-ring token, clipboard fallback) must be replicated in 3 + 2 copies; drift is already real (AddMailboxDrawer's TextField omits `required`), so an a11y/validation fix to one silently misses the others.
*Evidence:* `function TextField(props: { id: string; label: string; value: string; onChange: (value: string) => void; ...`
*Verdict:* CONFIRMED.

**`frontend/src/support/SupportAccessPanel.tsx:109` — the break-glass `reason` input has only a placeholder, no `<label>`/`aria-label`.**
*Failure:* A screen-reader user gets a field whose only accessible name is the placeholder (no visible label once text is entered — WCAG 3.3.2), inconsistent with every other labeled input in the app (incl. the analogous erase-reason field).
*Evidence:* `<input type="text" value={reason} onChange={...} maxLength={500} placeholder="Reason (e.g. investigate failed sync, ticket #4821)"`
*Verdict:* CONFIRMED.

**`frontend/src/connect/useConnectors.ts:46` — ~40 lines of controller scaffolding (`statusOf`, `isAuthFailure`, `GENERIC_MESSAGE`, the seq-guarded silent reload, the runAction try/catch) duplicated from `useCompanyUsers`; the three route guards triplicate the loading-skeleton + redirect block.**
*Failure:* A policy change to auth-failure handling (treat 403 as a notice) or a seq-guard fix must be applied in two hook copies; fixing one leaves the other on old behavior, invisibly (copies are line-for-line identical today). Security-adjacent.
*Evidence:* `function statusOf(error: unknown): number { return error instanceof AuthRequestError ? error.status : 0; }` / `function isAuthFailure(status: number): boolean { return status === 401 || status === 403; }`
*Verdict:* CONFIRMED.

**`frontend/src/HomePage.tsx:78` — page-level Framer Motion entrance animations (5 screens) don't respect `prefers-reduced-motion`.**
*Failure:* A reduced-motion user still gets a 0.6s `translateY` slide-up on every screen; the CSS `animation: none !important` kill-switch can't reach Framer's inline transforms (the repo's own `pageTransition.ts` documents this), and no `useReducedMotion`/`MotionConfig` guards these inner entrances. (LoginPage is a 6th instance.) Violates Motion Principle 5.
*Evidence:* `initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1] }}`
*Verdict:* CONFIRMED.

**`frontend/src/platform/platformClient.ts:37` — every API response cast `as T` with zero runtime shape validation, across all five clients.**
*Failure:* If the backend changes a response shape (wraps `GET /platform/orgs` in `{items:[...]}`), the cast accepts it and the failure surfaces as a render-time `organizations.filter is not a function` far from the fetch, not a clear boundary error. Co-located Pydantic models make this a dev-time risk.
*Evidence:* `return (await response.json()) as T;`
*Verdict:* CONFIRMED.

**`frontend/src/identity/LoginPage.tsx:97` — the login wordmark hardcodes two off-palette hexes (`#2389bd`, `#636ff6`) as inline styles instead of aurora tokens / `.text-brand-gradient`.**
*Failure:* A brand-palette update leaves these hand-interpolated gradient midpoints stale and the wordmark drifts off-brand; the design rule mandates aurora tokens only. (`#636ff6` is the exact RGB midpoint of brand-blue/brand-purple.)
*Evidence:* `<span style={{ color: "#2389bd" }}>n</span> ... <span style={{ color: "#636ff6" }}>A</span>`
*Verdict:* CONFIRMED.

---

## 3. Rules-compliance scorecard

| Rule | Status | Assessment |
|---|---|---|
| **Tenant isolation** (org_id NOT NULL, FORCE RLS, app-scope, no unscoped path) | ⚠️ Strong, with gaps | Four layers genuinely enforced; no live HTTP-reachable cross-tenant leak. Defects are latent/dev: dev script writes on BYPASSRLS engine (H), ambient ContextVar fails open (M), `audit_log` SELECT exposed to tenant role (M), email-existence oracle (M/L). |
| **Layering** (routes→services→repos→models, no skips, routes ≤20 lines) | ✅ Pass | No layer-skip or business-logic-in-repository violation found across the review. Routes parse+delegate; services hold logic; repos are data-access only. |
| **Custom exceptions only** (no bare `raise Exception`) | ✅ Pass | No bare `Exception` raises found; domain-specific exception types used throughout (the gap is duplicated handler wiring, L, not raw exceptions). |
| **Secrets never in code/logs/errors** | ❌ Violated | Hardcoded platform-admin password in `DevCredentialsPanel` (H); runtime-role passwords logged via plaintext `ALTER ROLE` (M); VCS-tracked seed passwords reachable on staging (M). |
| **Docstrings / type hints** (4-section module header, public docstrings, no bare `Any`) | ⚠️ Mostly good | Headers present broadly, but several are stale/contradictory and load-bearing under A4 (`models.py` dedup invariant, cursor "step over", false dead-letter comment); one untyped `# type: ignore[no-untyped-def]` dispatcher. |
| **File sizes** (<500 hard / <300 target) | ✅ Pass (hard); ⚠️ warn band | No file ≥500 (CI green). 14 files in the 300–499 warn band; worst is `connector_sync_runner.py` at 414 with clean split seams. |
| **Cross-tenant test coverage** (negative test per tenant-scoped endpoint/service/repo method) | ❌ Violated | Missing at: erasure endpoint/service/repos (H), sync cursor+run repos (M), person write methods (M), `lock_active_admin_ids` (M), support-grant repo (L), plus a mock-boundary violation (M) and a tautological cap test (L). |

---

## 4. Recommended fix order (top 10, PR-sized)

**PR 1 — GDPR erasure correctness + test (closes the worst correctness/compliance gap).**
1. Wire connector + entity erasure into `ErasureService` (erasure-hook registry or explicit repo deletes) so the certificate is truthful — `erasure_service.py:115` (H).
2. Add `not admin.is_active` to the sudo re-auth — `erasure_service.py:108` (M).
3. Add the cross-tenant containment test (seed org B, assert its rows survive) + service/repo-layer negatives — `test_erasure_routes.py:142` (H).

**PR 2 — IMAP transport & first-sync robustness.**
4. Pass `ssl.create_default_context()` on the verify path — `client.py:69` (H, one line).
5. Chunk / range-compress the `UID FETCH` — `fetch_session.py:187` (H).
6. Attempt STARTTLS (or refuse plaintext LOGIN) on the non-SSL path — `fetch_session.py:241` / `client.py:71` (M); fix the literal-mailbox LIST parse — `fetch_session.py:157` (M).

**PR 3 — Event-loop offload (instance-wide availability).**
7. `asyncio.to_thread` for bcrypt (`auth_service.py:75` + 4 sites, H) and for `parse_email` (`email_ingest_service.py:79`, M); commit/rollback before `count_pending` (`connector_sync_runner.py:206`, M) and before the IMAP verify (`connector_service.py:133`, M).

**PR 4 — Frontend credential hand-off (data-loss UX).**
8. Disable close-while-submitting + add a cancelled/closed guard for the three drawers (`CreateUserDrawer.tsx:127` H, `OnboardCompanyDrawer.tsx:129` M, `AddMailboxDrawer.tsx:103` M); gate `CopyField`'s "Copied" on the resolved write (`CreateUserSuccess.tsx:32` + `OnboardSuccess.tsx` M).

**PR 5 — Production-safety gates.**
9. `{import.meta.env.DEV && <DevCredentialsPanel/>}` (`DevCredentialsPanel.tsx:25`, H); gate `seed_identity.py` on `requires_secure_secrets` (M); add `is_production` guard + `scoped_session` to `ingest_imap_dump.py:82` (H); `.replace("%","%%")` in `env.py:36` (M).

**PR 6 — RLS least-privilege & audit_log.**
10. Replace `GRANT ON ALL TABLES` with explicit per-table grants (drop tenant SELECT/UPDATE/DELETE on platform_admins/refresh_tokens/organizations/alembic_version) and add an `audit_log` RLS policy (`INSERT WITH CHECK ... + SELECT USING org_id=GUC`) — `0009_enforce_rls.py:88` + `0005_audit_log.py:68` (M); add platform-admin auth audit events (`platform_auth_service.py:90`, M).

*(Beyond the top 10: the remaining sync-runner correctness items — dead-letter/step-over reality vs. docstrings, disable-mid-sync "succeeded" mislabel, KDF hardening, the recurring frontend test/duplication/a11y findings — fold cleanly into follow-up PRs grouped by the same files.)*

---

## 5. Addendum — fix pass applied (2026-06-10, same day)

The recommended fix order (all 6 PR-chunks: every HIGH plus the named MEDIUMs) was implemented the same day by a 6-fixer orchestrated pass with per-chunk adversarial referees and two diff-wide regression reviewers. **52 files changed, +3363/−176** (uncommitted working tree; the pre-fix state is staged in the index).

**Fixed (audit § → implementation):**
1. **GDPR erasure (H ×2 + M)** — erasure-hook registry at `app/common/erasure_hooks.py` (identity never imports connectors/entities); `connector_erasure_repository` + `entity_erasure_repository` hooks registered in `create_app`; explicit children-first org-scoped DELETEs; certificate gains `erased_rows_by_table`; sudo re-auth checks `is_active` (dummy-hash timing parity) and runs async; route/service/repo cross-tenant containment tests added (`test_erasure_cross_tenant.py` seeds A+B across every PII table). **Fail-closed guard added post-review:** empty hook registry → `ErasureNotConfiguredError` (500), nothing deleted.
2. **IMAP transport (H ×2 + M ×2)** — `ssl.create_default_context()` on the verify path; STARTTLS-or-refuse (`ImapTlsUnavailableError`) on both non-SSL paths — credentials never travel cleartext; `UID FETCH` chunked with consecutive-range compression (`1:10000` atoms, bounded command lines); literal LIST tuples parsed to the true folder name. **Post-review hardening:** folder names round-trip as latin-1 bytes through `select` (imaplib's ASCII encoder would otherwise crash the whole sync on a raw-UTF-8 literal name); an unencodable name skips that folder only.
3. **Event-loop + transactions (H + M ×4)** — `verify_password_async`/`hash_password_async` (`asyncio.to_thread`) at every async bcrypt site; `parse_email` off-loop; transaction released before `count_pending`'s IMAP enumeration and before the 15s connection verify; platform-admin auth now writes `auth.login.success/.failure`, `auth.refresh`, `auth.logout` audit rows (PC-04a(a) closed).
4. **Frontend credential hand-off (H + M ×4)** — all three drawers block close (backdrop/✕/Escape) while submitting + guard late promise resolution; `CopyField` claims "Copied" only after the clipboard write resolves (failure state otherwise); success panels re-assert focus; `AddMailboxDrawer.test.tsx` created. **Post-review:** every authorized request is time-bounded (30s `AbortSignal.timeout` in `sendWithBearer`) so a blackholed network can't wedge a drawer.
5. **Production-safety gates (H ×2 + M ×2)** — `DevCredentialsPanel` render gated behind `import.meta.env.DEV` (prod builds safe by construction); `seed_identity.py` and `ingest_imap_dump.py` refuse under `requires_secure_secrets`; ingest script moved off the BYPASSRLS engine onto `scoped_session(org_id)`; alembic URL `%`-escaped.
6. **RLS least-privilege (M ×2)** — migration `0013_least_privilege_grants`: tenant role stripped off the platform plane (`platform_admins`/`refresh_tokens`/`organizations`/`alembic_version` — zero privileges), `audit_log` UPDATE/DELETE revoked + ENABLE/FORCE RLS with the `org_isolation` policy, 0009's future-table default-ACL auto-grant revoked (new tables fail closed; standing test `test_least_privilege_grants.py` enforces the explicit-grant convention).

**Verification:** backend **496 passed / 0 failed, coverage 93.21%** (in-container/`POSTGRES_HOST=localhost` — the audit-day "baseline failures" were host-side DNS artifacts, not real); frontend **223 passed / 0 failed (31 files)**; migration chain at `0013` head. Review found 3 diff regressions (1 medium, 2 low) — all three fixed and re-verified the same day (the three "post-review" items above).

**Not yet addressed (tracked backlog):** the remaining ~36 medium / ~45 low findings outside the fix order — sync-runner docstring/step-over reality, disable-mid-sync "succeeded" mislabel, credential-cipher KDF, refresh-token localStorage (FIX_BEFORE_PROD httpOnly item), email-existence oracle (AUD-04, deferred decision), FE client/TextField deduplication, catch-all route, a11y/reduced-motion items, and the missing cross-tenant tests on sync cursor/run repos, person UPDATE paths, support-grant repo, and member-403 support routes.
