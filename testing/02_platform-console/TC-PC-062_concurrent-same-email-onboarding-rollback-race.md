# TC-PC-062: Concurrent same-email onboarding — atomic rollback under race (no orphan org)

| Field | Value |
|---|---|
| **ID** | TC-PC-062 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | RACE — Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
The strongest onboarding race: prove that when many requests race for the **same admin
email** (but distinct slugs), (a) exactly one user is created, and critically (b) the 49
losers' org inserts are **rolled back** so no orphan organization is committed for a failed
admin insert. Tests the atomicity claim in `platform_auth_service.py` (~line 179) and the
unit-of-work rollback in `get_session`.

## Break hypothesis
`onboard_organization` inserts the org FIRST (line 160) and the admin user SECOND (line 169).
For a loser, the org insert succeeds but the user insert hits the `users.email` UNIQUE and
raises `IntegrityError`. If the whole transaction does NOT roll back (e.g. the org was
committed in a separate transaction, or `get_session` swallows the exception), an **orphan
org** remains — a permanent, ownerless tenant row with no admin. With 50 racers, up to 49
orphan orgs could leak. That orphan is the win (NEW high finding).

## Preconditions
- Live stack up; demo platform admin token (minted once, reused for all 50).
- Run-stamp prefix printed by the harness (`race062-<stamp>`); the contested email is
  `admin-<prefix>-shared@oneai.dev`. Each request uses a DISTINCT slug `<prefix>-<i>` so the
  email is the only collision and any committed org is unambiguously attributable.

## Steps
1. Mint one platform access token.
2. Choose ONE email `E`. Fire **50** concurrent `POST /platform/orgs` with **distinct** slugs
   (`<prefix>-0` … `<prefix>-49`) but the SAME `admin_email = E`.
3. `summarize()` the tally.
4. psql ground-truth (run-stamp filtered):
   - `SELECT count(*) FROM users WHERE email='E'` → MUST be 1.
   - `SELECT count(*) FROM organizations WHERE slug LIKE '<prefix>%'` → MUST be 1 (winner only).
   - `SELECT slug FROM organizations WHERE slug LIKE '<prefix>%'` → only the winner's slug.

## Expected result
Exactly **1 → 201** and **49 → 409** (`{'detail':'A user with this email already exists.'}`);
zero 500s. DB: exactly 1 user for `E`, exactly 1 org under `<prefix>` (the winner), zero
orphans.

## Harness
Script: `harness/tc_062.py` · run: `docker compose exec -T backend python - < testing/02_platform-console/harness/tc_062.py`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> 50 concurrent same-email onboards (distinct slugs) resolved to exactly one 201 and 49
> "email already exists" 409s. DB ground-truth confirms exactly one user for the contested
> email AND exactly one organization (the winner `-0`) among the 50 distinct slugs — the 49
> losers' org inserts all rolled back. No orphan org committed for a failed admin insert.

**Evidence**

```
RUN-STAMP PREFIX: race062-19e8273fcd0c8d4
CONTESTED EMAIL: admin-race062-19e8273fcd0c8d4-shared@oneai.dev
TALLY: {201: 1, 409: 49}
SAMPLE 201 org: {'id': 'fdff6316-14c0-49b0-a25e-8632a3109854', 'slug': 'race062-19e8273fcd0c8d4-0'}
SAMPLE 201 admin email: admin-race062-19e8273fcd0c8d4-shared@oneai.dev
SAMPLE 409 BODY: {'detail': 'A user with this email already exists.'}
COUNTS  201: 1  409: 49  500: 0  EXC: {}
WINNER SLUG (only org that should exist): race062-19e8273fcd0c8d4-0
```

psql ground-truth (run-stamp filtered — the orphan check):

```
$ psql ... -c "SELECT count(*) AS users_for_email FROM users WHERE email='admin-race062-19e8273fcd0c8d4-shared@oneai.dev';"
 users_for_email
-----------------
               1

$ psql ... -c "SELECT count(*) AS orgs_for_prefix FROM organizations WHERE slug LIKE 'race062-19e8273fcd0c8d4%';"
 orgs_for_prefix
-----------------
               1

$ psql ... -c "SELECT slug FROM organizations WHERE slug LIKE 'race062-19e8273fcd0c8d4%' ORDER BY slug;"
           slug
---------------------------
 race062-19e8273fcd0c8d4-0
```

**Verdict**

The defense held — and this is the suite's strongest result. Under 50-way different-row
contention the atomic rollback is real: exactly one user for `E`, exactly one org (the
winner). The 49 losers each enter `onboard_organization`, insert their org
(`platform_auth_service.py:160`), then hit the `users.email` UNIQUE on the admin insert
(`:169`), raising `IntegrityError` → mapped to `DuplicateUserError` (`:178-181`) → 409. The
raised error propagates out of the request handler and `get_session`
(`backend/app/core/database.py:47-49`, `except Exception: await session.rollback(); raise`)
rolls back the WHOLE transaction — including the org insert — so no orphan tenant is
committed. Confirms AUD-05 and the FIX_BEFORE_PROD onboarding-atomicity claim. The 49
distinct UNIQUE-violation losers are the positive control proving contention truly engaged
(independent of any same-row serialization).

Honest framing (mechanism vs end-state): `count(orgs)==1` proves the **end state** — no
orphan org exists — which IS the contract. It does NOT independently prove the rollback PATH
fired on every loser, because a rolled-back insert leaves no artifact and a loser caught at
the pre-insert `email_exists` check (`:156`) inserts no org at all yet returns the identical
409. The HTTP layer cannot distinguish a pre-check rejection from a rollback-path rejection,
and (unlike TC-PC-061's 49 slug-UNIQUE 409s, a real positive control) there is no observable
artifact that an org was inserted-then-rolled-back. Given Postgres concurrent-UNIQUE blocking
semantics under `asyncio.gather` the rollback path almost certainly did fire for the racers
that passed the pre-check before the winner committed — but this is inferred, not traced.
What IS unambiguous: `count == 1` (no orphan), and a `count > 1` would have been a NEW high
finding (orphan org leak). The invariant holds.

**Notes / follow-up**

Pairs with TC-PC-061 (same-slug, no second insert to roll back). If a future refactor moves
the org commit into its own transaction, this case must be re-run — it would regress to
orphan orgs.
