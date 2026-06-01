# Target 05 — Org Lifecycle (PC-03a) — Adversarial Validation

> Dynamic, adversarial validation of the **PC-03a org-lifecycle** backend against the **live stack** —
> the per-company detail endpoint, status (suspend/reactivate) + legal-hold PATCHes, and the
> **suspend-blocks-login** gate. Companion to the static review
> `docs/audits/2026-06-01_platform-lifecycle-pr3a-review.md` (7 findings, 0 functional/security defects)
> and the epic `docs/PM/platform-console/EPIC-PC-03a-org-lifecycle.md`. Case code **`OL`** (`TC-OL-NNN`).
>
> See `testing/README.md` for strategy/legend/tags. Methodology: `.claude/skills/adversarial-validation/`.

## Scope

The platform console's first **lifecycle controls** over a tenant (the separate `aud='platform'` domain).

**In scope (backend):**
- **Suspend gate (⭐ the security core):** a `suspended` org blocks **login + refresh** (403) — raised
  **only after** the bcrypt credential check, so it is **not an enumeration oracle** (wrong-pw → generic
  401). `/auth/me` (and the whole company access-token path) stays **ungated** (deliberate asymmetry).
- **The high-value edge:** does a pre-suspension **refresh token survive** a suspension-403 and rotate
  again after reactivation? (`consume` stages the revoke *before* the suspend check raises — correctness
  hinges on the `get_session` rollback.)
- **Full blast radius:** the pre-suspension **access** token under suspension still reaches `/users` etc.
  → suspension is immediate for *new* sessions, eventual (≤ access-TTL, ~15 min) for *in-flight* ones.
- **Endpoints:** `GET /platform/orgs/{id}` (7-field metadata, unknown→404), `PATCH …/status`
  (enum-pinned, invalid→422), `PATCH …/legal-hold` (persist read-back).
- **Cross-domain (⭐):** company token → 401 on all three new endpoints (discriminating, no state change);
  **forged dev-secret platform token suspends/legal-holds any org** (DoS/compliance blast radius — the
  tracked *Rotate JWT_SECRET*).
- **Concurrency:** suspend-vs-login window (no token issued *after* the suspend commits);
  refresh-after-suspend (≥50, all 403).

**Out of scope:** the `organization_governance` posture table/editor → PC-03b; `offboarded` access-cutoff →
PC-06; audit logging → PC-04; the `/platform/orgs/:id` **frontend** detail screen → a separate Playwright pass.

## Environment

- Live stack: `docker compose up` → API `:8000`, db `:5432`. Branch `feat/platform-lifecycle` (migration `0004`).
- Harness runs inside the backend container against real uvicorn, self-contained over stdin:
  `cat testing/05_org-lifecycle/harness/_common.py testing/05_org-lifecycle/harness/<script>.py | docker compose exec -T backend python -`
- psql ground-truth on the **db** container: `docker compose exec -T db psql -U oneai -d oneai -c "<SQL>"`.
- Demo platform admin `super@ethera.ai` / `Sup3r-Dev-Only-2026!` — onboard fresh orgs only; **never mutated**.

## Key facts (the levers)

- Only `suspended` blocks (`onboarding`/`offboarded` are allowed to log in — offboarded cutoff is PC-06).
- The suspend 403 is raised after the credential check ⇒ no enumeration oracle.
- The company **access-token** path (`/auth/me`, `/users`, …) does **not** re-check org status — only
  login + refresh do (no access-token denylist; tracked in `FIX_BEFORE_PROD.md`).
- RLS is inert ⇒ the JWT secret is the single isolation layer; the dev secret is the forgeable default, so a
  forged platform token passes `get_current_platform_admin` (which never checks the admin exists) and can
  drive the new write endpoints.
- **HARD RULE:** never PATCH the `demo`/`globex` orgs — suspending them breaks the demo logins. Suspend only
  fresh run-stamped orgs you onboarded.

## Status dashboard

> Result: ⬜ not run · ✅ pass (defense held) · ❌ fail (a defect — the win) · ⚠️ pass-with-concern.
> Tag: 🆕 NEW · ✔ CONFIRMS-FIXED · ✖ REFUTES-FIX · 📋 CONFIRMS-DOCUMENTED · — n/a.
> **Run 2026-06-01.** **31 cases · 26 ✅ · 3 ⚠️ · 2 ❌ (both the documented forged-secret DoS) · 0 🆕 · 0 REFUTES-FIX.**
> Matches the PC-03a static review (zero functional/security defects). The high-value transaction question
> — does a suspension-failed refresh **burn** the token? — was settled live: **it survives** (TC-OL-003).
>
> *Method note:* phase-1 agents authored/ran the cases but a workflow harness error (no `StructuredOutput`)
> aborted the run before the SUSPEND/XDOM tails + the RACE phase; the lead finished those cases first-hand
> with the proven harness (`harness/_finish_suspend.py`, `_finish_race.py`).

| Suite | Cases | Result spread | NEW | Notes |
|---|---|---|---|---|
| SUSPEND — suspend-blocks-login gate ⭐ | 001–008 (8) | 6 ✅ · 2 ⚠️ | 0 | no-oracle (001), refresh-blocked (002), **refresh survives the 403** (003), `/auth/me`+`/users` asymmetry (004/005), offboarded-gap (006), e2e (007), reversible (008) |
| XDOM — cross-domain + forged blast radius ⭐ | 020–025 (6) | 4 ✅ · 2 ❌ | 0 | company token → 401 + no-write (020–023, discriminating, psql-verified); **forged dev-secret token suspends/legal-holds any org** (024/025) |
| CONTRACT — detail/status/legal-hold/authz | 030–043 (14) | 13 ✅ · 1 ⚠️ | 0 | 7-field metadata/404/422, enum-pin, persist read-back; legal-hold lax-bool coercion (037, NA) |
| RACE — suspend-vs-login + refresh-after-suspend | 060–062 (3) | 3 ✅ | 0 | no token after commit (060), 50×403 (061), same-row PATCH integrity (062) |

### Headline (lead-verified first-hand)

| ID | Result | Finding |
|---|---|---|
| TC-OL-003 ⭐ | ✅ | A pre-suspension refresh token **survives** the suspension-403 and rotates after reactivation — the staged `revoke_by_hash` is rolled back by `get_session` (psql: `revoked_at IS NULL`). The transaction question the static review couldn't settle. |
| TC-OL-024/025 | ❌ 📋 | Forged dev-secret platform token **suspends any org (availability kill) and sets legal holds (compliance)** — tracked *Rotate JWT_SECRET* + *Enforce RLS*, now on the new write surface. |
| TC-OL-005 | ⚠️ 📋 | Suspension is **eventual (≤ access-TTL) for in-flight sessions** — the access path (`/users`, `/auth/me`) never re-checks org status; tracked *access-token denylist*. "Suspended = ≤15 min to full cutoff." |
| TC-OL-060 | ✅ 📋 | Suspend-vs-login window is **bounded**: no token issued after the commit; race-won tokens can't refresh. |

## Coverage → PC-03a acceptance criteria (dynamically proven live)

| AC | Criterion | Dynamic proof |
|---|---|---|
| PC-03a-AC1 | status enum-pinned (invalid→422); suspend↔reactivate | ✅ TC-OL-033/034/007 |
| PC-03a-AC2 | detail = metadata only (7 fields); unknown→404 | ✅ TC-OL-030/031/032 |
| ⭐ PC-03a-AC3 | suspended blocks login+refresh (403); reachable only with valid creds | ✅ TC-OL-001/002/061 (+003 survives) |
| ⭐ PC-03a-AC4 | `/auth/me` still 200 under suspension (asymmetry) | ✅ TC-OL-004 (full radius TC-OL-005) |
| ⭐ PC-03a-AC5 | PATCH reaches the auth gate end-to-end | ✅ TC-OL-007 |
| ⭐ PC-03a-AC6 | company token → exactly 401 on all three new endpoints | ✅ TC-OL-020/021/022/023 |
| PC-03a-AC7 | legal hold sets + persists (read-back) | ✅ TC-OL-036 (auth-inert: TC-OL-039) |
