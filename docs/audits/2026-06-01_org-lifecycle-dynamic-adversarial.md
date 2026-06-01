# Org Lifecycle (PC-03a) — Dynamic Adversarial Validation

> **Scope:** the PC-03a org-lifecycle backend (branch `feat/platform-lifecycle`, migration `0004`) — the
> `OrganizationStatus` enum + `legal_hold`, `GET /platform/orgs/{id}`, `PATCH …/status`, `PATCH …/legal-hold`,
> and the **suspend-blocks-login** gate (login + refresh). Dynamic complement to the static review
> `2026-06-01_platform-lifecycle-pr3a-review.md` (7 findings, 0 functional/security defects) and the epic
> `docs/PM/platform-console/EPIC-PC-03a-org-lifecycle.md`.
>
> **Method:** a right-sized 4-suite `Workflow` (SUSPEND / XDOM / CONTRACT in parallel, RACE alone). The
> agents authored + ran their cases against the live stack, but a workflow-harness error (subagents
> completed without emitting `StructuredOutput`) aborted the run before the SUSPEND/XDOM tails and the RACE
> phase. The **lead finished those cases first-hand** with the already-proven harness
> (`testing/05_org-lifecycle/harness/_finish_suspend.py`, `_finish_race.py`) and re-verified every headline
> result. Suite + per-case evidence: `testing/05_org-lifecycle/` (31 × `TC-OL-NNN_*.md`).

## 1. Executive summary

**31 cases · 26 ✅ · 3 ⚠️ pass-with-concern · 2 ❌ fail (the documented forged-secret DoS) · 0 🆕 · 0 REFUTES-FIX.**
The PC-03a implementation is **sound** — exactly matching the static review. Every acceptance criterion
(AC1–AC7) was proven **live**, the suspend gate is correctly **not an enumeration oracle**, audience
confinement holds discriminatingly on all three new endpoints, and the detail endpoint is content-blind.

The marginal value of this dynamic pass over the static review:
- **Settled the transaction question the static review could not (TC-OL-003 ⭐):** does a suspension-failed
  refresh **burn** the token? `AuthService.refresh` stages `revoke_by_hash` *before* the suspend check
  raises — so correctness hinges on the `get_session` rollback. **Answer: the token survives.** Black-box
  (403 → reactivate → same token rotates 200) *and* psql ground-truth (`revoked_at IS NULL`, `survived=t`).
- **Characterized the full suspension blast radius (TC-OL-005):** suspension is immediate for *new* sessions
  but **eventual (≤ access-TTL, ~15 min) for in-flight** ones — the access path never re-checks org status.
- **Bounded the suspend-vs-login race (TC-OL-060):** no token issued after the commit; race-won tokens can't refresh.
- **Demonstrated the forged-token blast radius on the new write surface (TC-OL-024/025).**

## 2. Results by suite

| Suite | Cases | ✅ | ⚠️ | ❌ |
|---|---|---|---|---|
| SUSPEND — suspend-blocks-login gate ⭐ | 001–008 | 6 | 2 | |
| XDOM — cross-domain + forged blast radius ⭐ | 020–025 | 4 | | 2 |
| CONTRACT — detail/status/legal-hold/authz | 030–043 | 13 | 1 | |
| RACE — concurrency | 060–062 | 3 | | |
| **Total** | **31** | **26** | **3** | **2** |

## 3. The documented deferral, on the new write surface (the ❌ wins)

### Forged dev-secret platform token suspends / legal-holds any org — Critical (tracked)
*Cases TC-OL-024 (status), TC-OL-025 (legal-hold). CONFIRMS-DOCUMENTED. Lead-verified.*

A platform-aud token forged with the public dev `JWT_SECRET` and a **random non-existent `sub`** drives the
new write endpoints: `PATCH …/status {suspended}` → **200**, and a real company login on that org is then
**403** (the forged write reached and drove the company auth gate); `PATCH …/legal-hold {true}` → **200**,
read-back confirms it persisted. Root: `get_current_platform_admin` (`dependencies.py:103-117`) verifies only
signature + `aud` + expiry, never that the admin exists, and RLS is inert (superuser) → the JWT secret is the
**single** isolation layer. **Blast radius on this surface:** a leaked/forged dev secret is now a
**platform-wide availability kill** (suspend every customer) and a **compliance-tampering** primitive (set
legal holds, which beat right-to-erasure in PC-06). Tracked: `FIX_BEFORE_PROD.md` → *Rotate `JWT_SECRET`*
(+ *Enforce RLS*). The audience gate itself is correct — a forged **company**-aud token is rejected (401,
TC-OL-021/022, no write).

## 4. Notable characterizations (no defect — all CONFIRMS-DOCUMENTED / NA)

- **Suspend→reactivate is a *pause*, not a session reset (flip-side of TC-OL-003).** Because the
  suspension-403 rolls back the staged revoke (correct — no accidental destruction), a pre-suspension 7-day
  refresh token **springs back to life on reactivation**. Defensible design, but note the operator semantics:
  if an org is ever suspended *for cause* (compromise, legal, non-payment), lifting the suspension silently
  restores every pre-existing session — there is no rotation/kill on reactivation, consistent with the
  no-access-token-denylist / no-refresh-family-revoke deferrals. Worth designing around in PC-03b/PC-06.
- **Suspension is eventual for in-flight sessions (TC-OL-005, ⚠️).** Under suspension, a pre-suspension
  company-admin access token still reaches `/users` (200) and `/auth/me` (200) until it expires; only login +
  refresh gate on status, and there is no access-token denylist. Operator-facing meaning: **"suspended = ≤15
  min to full cutoff," not instant.** Tracked: *Add an access-token denylist*.
- **`offboarded`/`onboarding` don't block login (TC-OL-006, ⚠️).** The gate keys on `suspended` only
  (`_load_loginable_org`); `offboarded` *reads* terminal but still permits login until PC-06's erasure/cutoff.
- **`legal_hold` is auth-inert (TC-OL-039).** A legal hold is metadata that will block erasure (PC-06); it
  never gates login/refresh today — as designed.
- **`legal_hold` uses Pydantic v2 lax-bool coercion (TC-OL-037, ⚠️ NA).** `'yes'`/`'true'`/`1` → `True` (200);
  `2`/`'maybe'` → 422; no 500. The test designer's "→422 for `'yes'`" was the off expectation, not the code; a
  `StrictBool` is a product choice, not a security gap (legal_hold doesn't gate auth).
- **Suspend-vs-login window is bounded (TC-OL-060).** Of 60 concurrent logins racing a mid-batch suspend, the
  ones reading `active` before the commit minted tokens (benign); a fresh login after the batch → 403, and a
  race-won token's refresh → 403. No token issuable after the commit; window tokens bounded by §4's ≤TTL gap.

## 5. Empirical verdicts on the acceptance criteria (all held live)

AC1 (TC-OL-033/034/007), AC2 (TC-OL-030/031/032), **AC3** no-oracle + refresh-blocked (TC-OL-001/002/061,
discriminating byte-identical 401s), **AC4** `/auth/me` asymmetry (TC-OL-004), **AC5** PATCH→gate e2e
(TC-OL-007), **AC6** company token → exactly 401 on all three endpoints with psql no-write proof
(TC-OL-020/021/022/023), AC7 legal-hold persist read-back (TC-OL-036). The token-validation matrix on the new
routes (missing-bearer→401-not-403, alg=none, expired, malformed→401-not-500) holds on all three (TC-OL-040/041/042/043).

## 6. Coverage & limitations

- **Backend only.** The PC-03a **frontend** detail screen (`/platform/orgs/:id`, suspend/reactivate +
  legal-hold toggles, back-nav, 401→logout) is a **separate Playwright pass** — not covered here.
- **Same-row caveat.** TC-OL-062 (concurrent status PATCH) serializes on the row lock by design →
  corroboration, not independent proof; the DB `CHECK` is the backstop.
- **Workflow-harness incident.** The fan-out aborted on a `StructuredOutput` emission error after the agents
  had done the work; the lead finished + verified the remaining cases directly. No result depends on an
  unverified agent claim — every headline was re-run first-hand.

## 7. Post-run state (dev DB)

- Demo platform admin + `demo`/`globex` orgs — **untouched** (the HARD rule held: only run-stamped orgs were
  suspended, each reactivated after its case).
- Run-stamped lifecycle test orgs remain (`sus0NN-*`, `xdom0NN-*`, `race06N-*`, `probe-*`), all left `active`
  with `legal_hold=false`. A `TRUNCATE` + re-seed restores a clean demo.
