# Target 01 — Infrastructure + Authentication / Authorization

> Adversarial validation of the live Identity & Access module and its surrounding
> scaffold. See [`../README.md`](../README.md) for the strategy, result legend, and
> finding tags. Consolidated findings: [`docs/audits/2026-05-31_identity-dynamic-adversarial.md`](../../docs/audits/2026-05-31_identity-dynamic-adversarial.md).

## Result — 46 cases executed (2026-05-31)

**32 ✅ Pass · 5 ⚠️ Pass-with-concern · 9 ❌ Fail (defects reproduced) · 0 ✖ REFUTES-FIX.**

**3 NEW defects** (all independently re-verified): 🆕 **DYN-01** last-admin guard TOCTOU →
0-admin lockout (TC-IA-050/051/052), 🆕 **DYN-02** email never canonicalized → duplicate
identities + case-fragile login (TC-IA-064), 🆕 **DYN-03** NUL byte in `full_name` → HTTP 500
(TC-IA-066). Plus 1 Info hardening note (TC-IA-033, no `extra="forbid"`).
Every prior-audit fix that could be exercised **held** (AUD-01/05/02/08, audience split, full
token matrix). 6 documented deferrals reproduced once as live evidence.

> **⟳ Update (2026-05-31): all 4 NEW findings FIXED + re-verified.** DYN-01 (TC-IA-050/051/052)
> → atomic `FOR UPDATE` last-admin guard; harnesses now show **0 lockouts** (`{204+409}` /
> `{200+409}` / one-winner mixed). DYN-02 (TC-IA-064) → email lowercased; 2nd create **409**,
> opposite-case login **200**. DYN-03 (TC-IA-066) → NUL rejected **422** (+ global `DataError`→422
> net). DYN-04 (TC-IA-033) → `extra="forbid"` added. Each case file carries its remediation +
> re-run evidence; backend suite **115 passed** (97.8% cov). Consolidated:
> [`docs/audits/2026-05-31_identity-dynamic-adversarial.md`](../../docs/audits/2026-05-31_identity-dynamic-adversarial.md).

## Scope

**In scope** — the running stack (`http://localhost:8000`): infrastructure (`/health`,
`MaxBodySizeMiddleware`, CORS, RLS *defined-but-inert*, error-path hygiene), authentication
(company + platform login, no-enumeration, inactive accounts, password byte-limit, refresh
lifecycle, logout), authorization (role gate, the company/platform audience split, token
validation, missing-bearer→401, and **cross-tenant isolation** incl. JWT-forgery blast radius).

**Out of scope (own targets later):** user-management business rules beyond authZ, exhaustive
input fuzzing, the React frontend, connectors/retrieval.

## Environment & method

- Driver: self-contained Python (`httpx`/`asyncio`/`pyjwt`) harness piped into the
  **backend container** over stdin, hitting the **real uvicorn**:
  `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/<script>.py`
- Shared helpers: [`harness/_common.py`](harness/_common.py) — inlined into each `tc_<NNN>.py`.
- **Isolation:** persistent shared DB → every run provisions fresh **run-stamped** orgs and
  unique emails; the demo org's admin is never mutated. Concurrency cases ran 30–60 iterations.

## Status dashboard

**Legend:** Result ⬜ not run · ✅ pass (defense held) · ❌ fail (defect found — a *win*) · ⚠️ pass-with-concern.
Tag: 🆕 NEW · ✔ CONFIRMS-FIXED · ✖ REFUTES-FIX · 📋 CONFIRMS-DOCUMENTED · — n/a.

### Infrastructure
| ID | Title | Type | Result | Tag |
|---|---|---|---|---|
| [TC-IA-001](TC-IA-001_health-db-reachable.md) | `/health` reports DB reachable | Positive | ✅ | — |
| [TC-IA-002](TC-IA-002_oversized-body-413.md) | Oversized body (Content-Length) → 413 | Adversarial | ✅ | ✔ |
| [TC-IA-003](TC-IA-003_chunked-body-bypass.md) | Chunked / no-Content-Length large body bypasses cap | Adversarial | ⚠️ | 📋 |
| [TC-IA-004](TC-IA-004_rls-defined-but-inert.md) | RLS is defined but inert (superuser bypass) | Adversarial | ⚠️ | 📋 |
| [TC-IA-005](TC-IA-005_cors-posture.md) | CORS — credentialed wildcard methods/headers posture | Adversarial | ⚠️ | 📋 |
| [TC-IA-006](TC-IA-006_error-path-hygiene.md) | Error paths (404/405/422) leak no stack/secret | Negative | ✅ | — |

### Authentication
| ID | Title | Type | Result | Tag |
|---|---|---|---|---|
| [TC-IA-010](TC-IA-010_login-valid.md) | Company login (valid) → tokens + user view | Positive | ✅ | — |
| [TC-IA-011](TC-IA-011_login-wrong-password.md) | Login wrong password → 401 generic | Negative | ✅ | — |
| [TC-IA-012](TC-IA-012_login-unknown-email-no-enumeration.md) | Login unknown email → identical 401 (no enumeration) | Adversarial | ✅ | ✔ |
| [TC-IA-013](TC-IA-013_login-inactive-account.md) | Login inactive account → 401 generic | Negative | ✅ | — |
| [TC-IA-014](TC-IA-014_login-overlong-password-no-500.md) | Login >72-byte password → 401 not 500 | Boundary | ✅ | ✔ |
| [TC-IA-015](TC-IA-015_platform-login.md) | Platform login valid / wrong password | Positive | ✅ | — |
| [TC-IA-016](TC-IA-016_login-no-hash-leak-and-timing.md) | Login response leaks no hash; timing sanity | Adversarial | ⚠️ | — |

### Authorization
| ID | Title | Type | Result | Tag |
|---|---|---|---|---|
| [TC-IA-020](TC-IA-020_member-on-users-403.md) | Member token on `/users` → 403 | Negative | ✅ | — |
| [TC-IA-021](TC-IA-021_admin-on-users-200.md) | Admin token on `/users` → 200 (control) | Positive | ✅ | — |
| [TC-IA-022](TC-IA-022_company-token-on-platform-401.md) | Company token on `/platform/orgs` → 401 (wrong aud) | Adversarial | ✅ | ✔ |
| [TC-IA-023](TC-IA-023_platform-token-on-company-401.md) | Platform token on `/auth/me` + `/users` → 401 (wrong aud) | Adversarial | ✅ | ✔ |
| [TC-IA-024](TC-IA-024_missing-bearer-401.md) | Missing bearer → 401 (not 403) | Negative | ✅ | ✔ |
| [TC-IA-025](TC-IA-025_alg-none-token.md) | `alg=none` token → 401 | Adversarial | ✅ | ✔ |
| [TC-IA-026](TC-IA-026_tampered-signature.md) | Tampered signature → 401 | Adversarial | ✅ | ✔ |
| [TC-IA-027](TC-IA-027_expired-token.md) | Expired token → 401 | Negative | ✅ | ✔ |
| [TC-IA-028](TC-IA-028_missing-required-claim.md) | Token missing exp / aud / sub → 401 | Adversarial | ✅ | ✔ |
| [TC-IA-029](TC-IA-029_malformed-claims-no-500.md) | Malformed claims (non-UUID sub/org) → 401 not 500 | Adversarial | ✅ | ✔ |

### Cross-tenant isolation (hardest rule)
| ID | Title | Type | Result | Tag |
|---|---|---|---|---|
| [TC-IA-030](TC-IA-030_patch-cross-tenant-404.md) | Admin A PATCH B's user → 404 | Adversarial | ✅ | ✔ |
| [TC-IA-031](TC-IA-031_delete-cross-tenant-404.md) | Admin A DELETE B's user → 404 | Adversarial | ✅ | ✔ |
| [TC-IA-032](TC-IA-032_list-only-own-org.md) | Admin A `GET /users` → only A's users | Adversarial | ✅ | ✔ |
| [TC-IA-033](TC-IA-033_org-id-not-smuggable.md) | `org_id` cannot be smuggled via create body | Adversarial | ⚠️→✅ | 🆕 |
| [TC-IA-034](TC-IA-034_cross-tenant-email-oracle.md) | Cross-tenant email-existence oracle (409 vs 201) | Adversarial | ❌ | 📋 |
| [TC-IA-035](TC-IA-035_forged-token-reads-other-org.md) | Forged token (org_id=B) → reads+writes B's users | Adversarial | ❌ | 📋 |
| [TC-IA-036](TC-IA-036_forged-role-escalation.md) | Forged role upgrade (member→admin) lifts 403 | Adversarial | ❌ | 📋 |

### Token lifecycle
| ID | Title | Type | Result | Tag |
|---|---|---|---|---|
| [TC-IA-040](TC-IA-040_refresh-single-use-serial.md) | Refresh rotation single-use (serial reuse → 401) | Negative | ✅ | ✔ |
| [TC-IA-041](TC-IA-041_refresh-excludes-user.md) | Refresh returns new pair, excludes `user` | Positive | ✅ | — |
| [TC-IA-042](TC-IA-042_logout-idempotent.md) | Logout idempotent (twice → 204/204) | Positive | ✅ | — |
| [TC-IA-043](TC-IA-043_refresh-after-logout-401.md) | Refresh after logout → 401 | Negative | ✅ | ✔ |
| [TC-IA-044](TC-IA-044_deactivated-access-token-me-401.md) | Deactivated user's access token on `/auth/me` → 401 | Negative | ✅ | ✔ |
| [TC-IA-045](TC-IA-045_deactivated-refresh-401.md) | Deactivated user's refresh token → 401 | Negative | ✅ | ✔ |
| [TC-IA-046](TC-IA-046_demoted-admin-keeps-power.md) | Demoted admin keeps admin power until token expiry | Adversarial | ❌ | 📋 |

### Concurrency races (dedicated, many iterations)
| ID | Title | Type | Result | Tag |
|---|---|---|---|---|
| [TC-IA-050](TC-IA-050_last-admin-race-delete-delete.md) | Last-admin race: concurrent DELETE+DELETE → 0 admins | Concurrency | ❌→✅ | 🆕 |
| [TC-IA-051](TC-IA-051_last-admin-race-patch-patch.md) | Last-admin race: concurrent PATCH→member ×2 → 0 admins | Concurrency | ❌→✅ | 🆕 |
| [TC-IA-052](TC-IA-052_last-admin-race-mixed.md) | Last-admin race: mixed PATCH+DELETE → 0 admins | Concurrency | ❌→✅ | 🆕 |
| [TC-IA-053](TC-IA-053_refresh-rotation-race.md) | Refresh rotation race: N concurrent → exactly 1 success | Concurrency | ✅ | ✔ |
| [TC-IA-054](TC-IA-054_create-user-dup-race.md) | Create-user duplicate race → 201+409, never 500 | Concurrency | ✅ | ✔ |
| [TC-IA-055](TC-IA-055_onboarding-integrity-race.md) | Onboarding IntegrityError (slug/email) → 409, no orphan org | Concurrency | ✅ | ✔ |

### Input validation on auth surfaces
| ID | Title | Type | Result | Tag |
|---|---|---|---|---|
| [TC-IA-060](TC-IA-060_pw-73-byte-422.md) | 73-byte ASCII password → 422 (not 500) | Boundary | ✅ | ✔ |
| [TC-IA-061](TC-IA-061_pw-multibyte-over-72-bytes.md) | Multibyte password >72 bytes / <72 chars → 422 | Boundary | ✅ | ✔ |
| [TC-IA-062](TC-IA-062_pw-boundaries.md) | Password byte/char boundaries (72 ok, 7 too short) | Boundary | ✅ | — |
| [TC-IA-063](TC-IA-063_role-escalation-via-body.md) | `role=platform_admin` via body → 422 (no escalation) | Adversarial | ✅ | — |
| [TC-IA-064](TC-IA-064_email-case-sensitivity.md) | Email case-sensitivity → duplicate identities / login fail | Adversarial | ❌→✅ | 🆕 |
| [TC-IA-065](TC-IA-065_injection-stored-literally.md) | SQL/script injection in name fields stored literally | Adversarial | ✅ | — |
| [TC-IA-066](TC-IA-066_field-bounds-fuzz.md) | NUL byte in `full_name` → HTTP 500 | Fuzz | ❌→✅ | 🆕 |
